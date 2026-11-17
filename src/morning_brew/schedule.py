"""Run gate and dead-man check (SPEC FR-001, FR-002)."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from morning_brew.domain import Stamp


@dataclass(frozen=True)
class GateDecision:
    run: bool
    reason: str


def should_run(now: datetime, stamp: Stamp | None, earliest_hour: int) -> GateDecision:
    """Run at most one full success per local calendar day, not before earliest_hour."""
    if now.hour < earliest_hour:
        return GateDecision(False, f"before earliest hour {earliest_hour}")
    if stamp is None or stamp.last_full_success is None:
        return GateDecision(True, "no full success recorded")
    if stamp.quality != "full":
        return GateDecision(True, "last run was degraded; retrying")
    last = stamp.last_full_success
    if last > now:
        return GateDecision(True, "future-dated stamp ignored")
    if last.astimezone(now.tzinfo).date() == now.date():
        return GateDecision(False, "already fully succeeded today")
    return GateDecision(True, "new calendar day")


def dead_man(now: datetime, stamp: Stamp | None, hours: int = 48) -> bool:
    """True when the last full success is older than ``hours``."""
    if stamp is None:
        return False
    reference = stamp.last_full_success or stamp.first_attempt
    if reference is None:
        return False
    return now - reference > timedelta(hours=hours)
