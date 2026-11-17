"""Run configuration. Defaults are the PLAN Global Constraints."""

import dataclasses
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from morning_brew.domain import ConfigError, ConfigPermissionError


def _default_state_dir() -> Path:
    return Path.home() / ".local" / "state" / "morning-brew"


@dataclass(frozen=True)
class Config:
    min_age_days: int = 3
    earliest_hour: int = 7
    start_interval: int = 3600
    dead_man_hours: int = 48
    run_budget_minutes: int = 20
    cleanup_runs: int = 2
    untrusted_text_cap: int = 2000
    local_num_predict: int = 400
    fnox_timeout_s: int = 10
    ollama_timeout_s: int = 120
    shadow_runs: int = 10
    trusted_taps: frozenset[str] = frozenset()
    chain_mode: Literal["shadow", "live"] = "shadow"
    local_model: str = "qwen3:4b"
    local_digest: str | None = None
    claude_model: str = "claude-sonnet-5-5"
    claude_effort: str = "low"
    claude_key_name: str = "ANTHROPIC_API_KEY"
    stale_hold_days: int = 14
    quiet_hours: tuple[int, int] = (22, 7)
    state_dir: Path = field(default_factory=_default_state_dir)
    fnox_config: Path | None = None


_INT_FIELDS = frozenset(
    {
        "min_age_days",
        "earliest_hour",
        "start_interval",
        "dead_man_hours",
        "run_budget_minutes",
        "cleanup_runs",
        "untrusted_text_cap",
        "local_num_predict",
        "fnox_timeout_s",
        "ollama_timeout_s",
        "shadow_runs",
        "stale_hold_days",
    }
)
_STR_FIELDS = frozenset(
    {"local_model", "claude_model", "claude_effort", "claude_key_name"}
)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _convert(key: str, value: Any) -> Any:
    if key in _INT_FIELDS:
        if not _is_int(value):
            raise ConfigError(f"config key {key!r} must be an integer")
        return value
    if key in _STR_FIELDS:
        if not isinstance(value, str):
            raise ConfigError(f"config key {key!r} must be a string")
        return value
    if key == "local_digest":
        if not isinstance(value, str):
            raise ConfigError("config key 'local_digest' must be a string")
        return value
    if key == "chain_mode":
        if value not in ("shadow", "live"):
            raise ConfigError('config key \'chain_mode\' must be "shadow" or "live"')
        return value
    if key == "trusted_taps":
        if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
            raise ConfigError("config key 'trusted_taps' must be a list of strings")
        return frozenset(t.lower() for t in value)
    if key == "quiet_hours":
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not all(_is_int(h) and 0 <= h <= 23 for h in value)
        ):
            raise ConfigError("config key 'quiet_hours' must be two integers 0-23")
        return (value[0], value[1])
    if key == "fnox_config":
        if not isinstance(value, str):
            raise ConfigError("config key 'fnox_config' must be a string path")
        return Path(value).expanduser()
    raise ConfigError(f"unknown config key {key!r}")


def load_config(path: Path | None, *, state_dir: Path) -> Config:
    """Load the TOML config. None or a missing file yields defaults.

    The file is code (trusted taps, chain mode), so one that is group/world
    writable or owned by someone else is refused.
    """
    if path is None or not path.exists():
        return Config(state_dir=state_dir)
    st = path.stat()
    if st.st_mode & 0o022:
        raise ConfigPermissionError(f"config {path} is group/world-writable")
    if st.st_uid != os.getuid():
        raise ConfigPermissionError(f"config {path} is not owned by the current user")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"config {path} is not valid TOML: {exc}") from exc
    valid = {f.name for f in dataclasses.fields(Config)} - {"state_dir"}
    values: dict[str, Any] = {}
    for key, value in data.items():
        if key == "state_dir":
            raise ConfigError(
                "config key 'state_dir' is not allowed; it comes from the caller"
            )
        if key not in valid:
            raise ConfigError(f"unknown config key {key!r}")
        values[key] = _convert(key, value)
    return Config(state_dir=state_dir, **values)


_BREW_BIN = "/opt/homebrew/bin"
_PASSTHROUGH = ("LANG", "LC_ALL", "TMPDIR", "USER", "LOGNAME")


def child_env(base: Mapping[str, str]) -> dict[str, str]:
    """Allowlisted environment for every subprocess; nothing else leaks."""
    entries = [_BREW_BIN]
    for entry in base.get("PATH", "").split(":"):
        if entry and entry not in entries:
            entries.append(entry)
    env = {
        "PATH": ":".join(entries),
        "HOMEBREW_NO_AUTO_UPDATE": "1",
        "HOMEBREW_NO_ANALYTICS": "1",
        "HOMEBREW_NO_ENV_HINTS": "1",
        "HOMEBREW_NO_INSTALL_CLEANUP": "1",
    }
    if "HOME" in base:
        env["HOME"] = base["HOME"]
    for key in _PASSTHROUGH:
        if key in base:
            env[key] = base[key]
    return env


_PYTHON_RE = re.compile(r"^python@\d+\.\d+$")


def _python_formula(path: Path) -> str | None:
    parts = path.parts
    for i, part in enumerate(parts[:-1]):
        if parts[i] in ("Cellar", "opt") and _PYTHON_RE.match(parts[i + 1]):
            return parts[i + 1]
    return None


def detect_self_deps(executable: Path) -> frozenset[str]:
    """Formulae this tool runs on; they must never be upgraded in-run."""
    deps = {"fnox"}
    for candidate in (executable, executable.resolve()):
        formula = _python_formula(candidate)
        if formula:
            deps.add(formula)
    return frozenset(deps)
