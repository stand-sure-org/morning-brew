"""Shadow-period report over the audit log (FR-057, DESIGN section 11).

Counts what the chain would have done on real (``decide``) runs. Dry runs,
eval and review events are excluded: the eval corpus records ``upgrade``
verdicts by design and would inflate the count. A deterministic suspicious
hold carries no ``would_be`` but is still a hold; a Claude outage is neither.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from morning_brew.domain import AuditEvent

_STAGE = "decide"
_SUSPICIOUS = "suspicious"


@dataclass(frozen=True)
class ShadowReport:
    runs: int
    would_upgrade: int
    would_hold: int
    disagreements: int


def _flag(event: AuditEvent, name: str) -> bool:
    return bool(event.booleans and event.booleans.get(name) is True)


def shadow_report(events: Iterable[AuditEvent]) -> ShadowReport:
    runs: set[str] = set()
    upgrade = hold = disagreements = 0
    for e in events:
        if e.stage != _STAGE:
            continue
        runs.add(e.run_id)
        if e.rule_id != "chain":
            continue
        stages = {m[0] for m in e.models}
        if _flag(e, "would_upgrade"):
            upgrade += 1
        elif _flag(e, "would_hold") or _SUSPICIOUS in stages:
            hold += 1
        if _flag(e, "disagreement"):
            disagreements += 1
    return ShadowReport(len(runs), upgrade, hold, disagreements)
