"""Live-process set: formulae whose daemons are running (FR-031).

Brew never restarts services, and ``brew services`` misses daemons started by
user-authored LaunchAgents, so liveness is the union of brew service status,
launchd plist executables and launchd-owned running executables, plus the
runtime-dependency closure of every live member.
"""

import logging
import plistlib
from collections.abc import Iterable
from pathlib import Path

from morning_brew.domain import CommandRunner, Snapshot

logger = logging.getLogger(__name__)

DEFAULT_PREFIX = Path("/opt/homebrew")
PS_TIMEOUT_SECONDS = 10.0
_ROOTS = ("Cellar", "opt")


def _short_name_of(path: Path, prefix: Path) -> str | None:
    resolved = path.resolve(strict=False)
    try:
        parts = resolved.relative_to(prefix.resolve(strict=False)).parts
    except ValueError:
        return None
    if len(parts) >= 2 and parts[0] in _ROOTS:
        return parts[1]
    return None


def _plist_executable(plist: Path) -> Path | None:
    try:
        with plist.open("rb") as fh:
            data = plistlib.load(fh)
    except Exception as exc:  # noqa: BLE001 - any parse/read failure means skip
        logger.warning("skipping unreadable launchd plist %s: %s", plist, exc)
        return None
    if not isinstance(data, dict):
        logger.warning("skipping launchd plist %s: not a dictionary", plist)
        return None
    args = data.get("ProgramArguments")
    if isinstance(args, list) and args and isinstance(args[0], str):
        return Path(args[0])
    program = data.get("Program")
    if isinstance(program, str):
        return Path(program)
    return None


def compute_live_set(
    snapshot: Snapshot,
    *,
    launch_plists: Iterable[Path],
    running_exes: Iterable[Path],
    prefix: Path = DEFAULT_PREFIX,
) -> frozenset[str]:
    by_short: dict[str, str] = {}
    for name in sorted(snapshot.installed_names):
        by_short.setdefault(name.rsplit("/", 1)[-1], name)

    def canonical(name: str) -> str | None:
        if name in snapshot.installed_names:
            return name
        return by_short.get(name.rsplit("/", 1)[-1])

    live: set[str] = set()
    for name, status in snapshot.services.items():
        if status != "none":
            live.add(canonical(name) or name)

    paths: list[Path] = []
    for plist in launch_plists:
        exe = _plist_executable(plist)
        if exe is not None:
            paths.append(exe)
    paths.extend(running_exes)
    for path in paths:
        short = _short_name_of(path, prefix)
        if short is not None and short in by_short:
            live.add(by_short[short])

    pending = list(live)
    while pending:
        current = pending.pop()
        for dep in snapshot.dep_graph.get(current, ()):
            resolved = canonical(dep) or dep
            if resolved not in live:
                live.add(resolved)
                pending.append(resolved)
    return frozenset(live)


def discover_launch_plists(home: Path) -> list[Path]:
    dirs = (
        home / "Library" / "LaunchAgents",
        Path("/Library/LaunchAgents"),
        Path("/Library/LaunchDaemons"),
    )
    found: list[Path] = []
    for directory in dirs:
        try:
            found.extend(sorted(directory.glob("*.plist")))
        except OSError as exc:
            logger.warning("cannot list %s: %s", directory, exc)
    return found


def list_running_executables(runner: CommandRunner) -> list[Path]:
    """Executables of processes whose parent is launchd (daemons only, R29)."""
    result = runner(["ps", "-axo", "ppid=,comm="], PS_TIMEOUT_SECONDS)
    if result.returncode != 0:
        logger.warning("ps failed with exit code %s", result.returncode)
        return []
    exes: list[Path] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0] == "1":
            exes.append(Path(parts[1]))
    return exes


class LiveProcessScanner:
    def __init__(
        self, runner: CommandRunner, home: Path, prefix: Path = DEFAULT_PREFIX
    ) -> None:
        self._runner = runner
        self._home = home
        self._prefix = prefix

    def live_set(self, snapshot: Snapshot) -> frozenset[str]:
        return compute_live_set(
            snapshot,
            launch_plists=discover_launch_plists(self._home),
            running_exes=list_running_executables(self._runner),
            prefix=self._prefix,
        )
