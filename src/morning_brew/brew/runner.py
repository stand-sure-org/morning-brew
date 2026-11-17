"""The one place a child process is started. Argv only, no shell, no stdin."""

import logging
import subprocess
from collections.abc import Mapping, Sequence

from morning_brew.domain import CommandResult

log = logging.getLogger(__name__)

TIMEOUT_RC = 124
NOT_FOUND_RC = 127


class SubprocessRunner:
    """A ``CommandRunner`` that runs argv with an allowlisted environment."""

    def __init__(self, env: Mapping[str, str]) -> None:
        self._env = dict(env)

    def __call__(self, argv: Sequence[str], timeout: float) -> CommandResult:
        args = list(argv)
        try:
            proc = subprocess.run(
                args,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                shell=False,
                capture_output=True,
                text=True,
                env=dict(self._env),
            )
        except subprocess.TimeoutExpired:
            log.warning("command timed out after %ss: %s", timeout, args[:2])
            return CommandResult(TIMEOUT_RC, "", f"timeout after {timeout}s")
        except FileNotFoundError as exc:
            log.warning("command not found: %s", args[:1])
            return CommandResult(NOT_FOUND_RC, "", f"not found: {exc}")
        return CommandResult(proc.returncode, proc.stdout or "", proc.stderr or "")
