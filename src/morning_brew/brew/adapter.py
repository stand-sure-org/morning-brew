"""BrewPort over the brew CLI: explicit package lists, ``--`` before names."""

import json
import logging
import time
from collections.abc import Callable, Sequence

from morning_brew.brew.parse import parse_dry_run
from morning_brew.brew.snapshot import build_snapshot
from morning_brew.components import require_valid
from morning_brew.domain import (
    BrewBusyError,
    BrewParseError,
    BrewUpdateError,
    CommandResult,
    CommandRunner,
    InvalidPackageName,
    Snapshot,
    UpgradeResult,
)
from morning_brew.sanitize import tail_redacted

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120.0
UPGRADE_TIMEOUT = 1800.0
UPDATE_BACKOFF = (5, 30)
_BUSY = "already running"
_PINNED_REFUSAL = "pinned package"


class BrewCli:
    """The only code that executes brew. Never a bare upgrade or cleanup."""

    def __init__(
        self,
        runner: CommandRunner,
        brew: str = "/opt/homebrew/bin/brew",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._run = runner
        self._brew = brew
        self._sleep = sleep

    def _call(
        self, args: Sequence[str], timeout: float = DEFAULT_TIMEOUT
    ) -> CommandResult:
        return self._run([self._brew, *args], timeout)

    def update(self) -> None:
        attempts = len(UPDATE_BACKOFF) + 1
        result: CommandResult | None = None
        for attempt in range(attempts):
            if attempt:
                self._sleep(UPDATE_BACKOFF[attempt - 1])
            result = self._call(["update"])
            if result.returncode == 0:
                return
            log.warning(
                "brew update attempt %d/%d failed (rc=%d)",
                attempt + 1,
                attempts,
                result.returncode,
            )
        tail = tail_redacted(result.stderr if result else "", 500)
        raise BrewUpdateError(f"brew update failed after {attempts} attempts: {tail}")

    def _read(self, args: Sequence[str]) -> str:
        result = self._call(args)
        if result.returncode != 0:
            tail = tail_redacted(result.stderr, 500)
            raise BrewParseError(
                f"brew {' '.join(args)} exited {result.returncode}: {tail}"
            )
        return result.stdout  # stderr noise (tap deprecation warnings) ignored

    def snapshot(self) -> Snapshot:
        try:
            outdated = json.loads(self._read(["outdated", "--json=v2"]))
            info = json.loads(self._read(["info", "--json=v2", "--installed"]))
            leaves = self._read(["leaves"]).split()
            services = json.loads(self._read(["services", "list", "--json"]))
            version_lines = self._read(["--version"]).strip().splitlines()
            version = version_lines[0].strip() if version_lines else ""
            if not version:
                raise BrewParseError("brew --version printed nothing")
            return build_snapshot(outdated, info, leaves, services, version)
        except BrewParseError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            # ValueError covers json.JSONDecodeError.
            raise BrewParseError(f"unreadable brew output: {exc!r}") from exc

    def upgrade(self, members: Sequence[str]) -> UpgradeResult:
        names = list(members)
        if not names:
            raise InvalidPackageName("refusing an upgrade with no package names")
        require_valid(names)
        result = self._call(["upgrade", "--", *names], UPGRADE_TIMEOUT)
        if _BUSY in result.stderr:
            raise BrewBusyError(tail_redacted(result.stderr, 500))
        ok = result.returncode == 0 and _PINNED_REFUSAL not in result.stderr
        if not ok:
            log.warning("brew upgrade failed (rc=%d) for %s", result.returncode, names)
        return UpgradeResult(ok=ok, stderr_tail=tail_redacted(result.stderr))

    def dry_run_closure(self, members: Sequence[str]) -> frozenset[str]:
        names = list(members)
        if not names:
            return frozenset()
        require_valid(names)
        result = self._call(["upgrade", "--dry-run", "--", *names])
        if result.returncode != 0:
            # Fail safe: an empty closure never equals the plan, so the
            # orchestrator excludes it with an anomaly instead of crashing.
            log.warning(
                "brew dry-run failed (rc=%d) for %s: %s",
                result.returncode,
                names,
                tail_redacted(result.stderr, 300),
            )
            return frozenset()
        return parse_dry_run(result.stdout)

    def cleanup(self, packages: Sequence[str]) -> None:
        names = list(packages)
        if not names:
            return  # a bare cleanup would clean everything
        require_valid(names)
        result = self._call(["cleanup", "--", *names])
        if result.returncode != 0:
            log.warning(
                "brew cleanup failed (rc=%d): %s",
                result.returncode,
                tail_redacted(result.stderr, 300),
            )
