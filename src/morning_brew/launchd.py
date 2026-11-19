"""LaunchAgent plist generation, install and uninstall."""

import contextlib
import logging
import os
import plistlib
import tempfile
from pathlib import Path

from morning_brew.domain import CommandRunner, InstallError

log = logging.getLogger(__name__)

LABEL = "com.morningbrew.run"
START_INTERVAL = 3600
_TIMEOUT = 30.0
_NOT_LOADED_HINTS = ("could not find", "no such process", "not found", "not loaded")


def plist_path(home: Path, label: str = LABEL) -> Path:
    return home / "Library" / "LaunchAgents" / f"{label}.plist"


def _log_dir(home: Path) -> Path:
    return home / "Library" / "Logs" / "morning-brew"


def render_plist(
    venv_bin: Path,
    home: Path,
    start_interval: int = START_INTERVAL,
    label: str = LABEL,
) -> bytes:
    """Render the LaunchAgent. No WorkingDirectory (S4), no secrets, no shell."""
    logs = _log_dir(home)
    data = {
        "Label": label,
        "ProgramArguments": [str(venv_bin), "run"],
        "StartInterval": start_interval,  # [R58] config start_interval
        "ProcessType": "Background",
        "EnvironmentVariables": {
            "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
            "HOME": str(home),
            "HOMEBREW_NO_AUTO_UPDATE": "1",
        },
        "StandardOutPath": str(logs / "stdout.log"),
        "StandardErrorPath": str(logs / "stderr.log"),
    }
    return plistlib.dumps(data)


def _write_bytes_atomic(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def install(
    runner: CommandRunner,
    venv_bin: Path,
    home: Path,
    uid: int,
    *,
    start_interval: int = START_INTERVAL,
) -> None:
    if not (venv_bin.is_file() and os.access(venv_bin, os.X_OK)):
        raise InstallError(f"{venv_bin} is missing or not executable; run `uv sync`")
    logs = _log_dir(home)
    logs.mkdir(parents=True, exist_ok=True)
    os.chmod(logs, 0o700)
    plist = plist_path(home)
    _write_bytes_atomic(plist, render_plist(venv_bin, home, start_interval), 0o644)
    # Best effort: an already-loaded agent must be replaced; "not loaded" is fine.
    runner(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], _TIMEOUT)
    res = runner(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], _TIMEOUT)
    if res.returncode != 0:
        raise InstallError(f"launchctl bootstrap failed: {res.stderr.strip()[:200]}")


def uninstall(runner: CommandRunner, home: Path, uid: int) -> None:
    res = runner(["launchctl", "bootout", f"gui/{uid}/{LABEL}"], _TIMEOUT)
    if res.returncode != 0 and not any(
        h in res.stderr.lower() for h in _NOT_LOADED_HINTS
    ):
        log.warning("launchctl bootout returned %s", res.returncode)
    plist_path(home).unlink(missing_ok=True)
