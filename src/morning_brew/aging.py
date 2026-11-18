"""Hold aging, stale escalation and the weekly digest (SPEC FR-064).

Only decided holds age (FR-061): transient holds are retried every run, so
they are counted but never stale and never blocked-by-dependency.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from morning_brew.domain import Hold, HoldKind
from morning_brew.textsafe import strip_control

DIGEST_INTERVAL = timedelta(days=7)


@dataclass(frozen=True)
class Aging:
    decided_count: int
    oldest_decided_days: int | None
    blocked_by_dep_count: int
    transient_count: int


def _age_days(hold: Hold, now: datetime) -> int:
    return (now - hold.first_held).days


def hold_aging(holds: Sequence[Hold], now: datetime) -> Aging:
    decided = [h for h in holds if h.kind is HoldKind.DECIDED]
    return Aging(
        decided_count=len(decided),
        oldest_decided_days=max((_age_days(h, now) for h in decided), default=None),
        blocked_by_dep_count=sum(1 for h in decided if h.blocked_by is not None),
        transient_count=sum(1 for h in holds if h.kind is HoldKind.TRANSIENT),
    )


def stale_holds(
    holds: Sequence[Hold], now: datetime, stale_after_days: int = 14
) -> list[Hold]:
    """Decided holds older than ``stale_after_days`` whole days (strictly)."""
    return sorted(
        (
            h
            for h in holds
            if h.kind is HoldKind.DECIDED and _age_days(h, now) > stale_after_days
        ),
        key=lambda h: h.name,
    )


def digest_due(last_digest: datetime | None, now: datetime) -> bool:
    return last_digest is None or now - last_digest >= DIGEST_INTERVAL


def render_digest(aging: Aging, stale: Sequence[Hold]) -> str:
    oldest = (
        f"oldest {aging.oldest_decided_days} days"
        if aging.oldest_decided_days is not None
        else "none aging"
    )
    lines = [
        "morning-brew weekly hold digest",
        f"{aging.decided_count} decided ({oldest}), "
        f"{aging.blocked_by_dep_count} blocked by a dependency, "
        f"{aging.transient_count} transient",
    ]
    if stale:
        lines.append("Stale holds:")
        # [R57] sent as a notification: names and versions only, never the
        # hold reason (it can derive from untrusted caveat or model text).
        lines.extend(
            f"- {strip_control(h.name)}: held at {strip_control(h.held_at_version)}"
            for h in stale
        )
    else:
        lines.append("No stale holds.")
    return "\n".join(lines)
