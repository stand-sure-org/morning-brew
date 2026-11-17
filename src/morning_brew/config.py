"""Run configuration. Defaults are the PLAN Global Constraints."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


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
