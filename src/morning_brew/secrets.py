"""Lazy secret fetch through ``fnox get`` (FR-054, ruling R19 / spike S4).

``fnox`` resolves its config from the current directory and a launchd job has
no reliable cwd, so the config path is always passed explicitly. The value is
returned to the caller only: it is never logged, never placed in an exception
message and never written to ``os.environ``.
"""

import logging
import re
from pathlib import Path

from morning_brew.domain import CommandRunner, SecretUnavailable

log = logging.getLogger(__name__)

FNOX_TIMEOUT = 10
# Anything config may carry, minus a leading "-" (fnox would read a flag).
_NAME = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


def fnox_get(name: str, runner: CommandRunner, config_path: Path) -> str:
    """Return the secret ``name`` or raise ``SecretUnavailable``."""
    if not _NAME.fullmatch(name):
        raise SecretUnavailable("invalid secret name")
    argv = ["fnox", "get", "--config", str(config_path), name]
    try:
        result = runner(argv, FNOX_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - any runner failure is "unavailable"
        log.warning("fnox get %s failed: %s", name, type(exc).__name__)
        raise SecretUnavailable(f"fnox get failed: {type(exc).__name__}") from None
    if result.returncode != 0:
        # stderr is not logged: it is fnox's text and could quote the value.
        log.warning("fnox get %s exited %s", name, result.returncode)
        raise SecretUnavailable(f"fnox get exited {result.returncode}")
    value = result.stdout.removesuffix("\n")
    if not value.strip():
        log.warning("fnox get %s returned an empty value", name)
        raise SecretUnavailable("fnox get returned an empty value")
    return value
