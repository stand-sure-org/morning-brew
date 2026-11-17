"""Deterministic rules. T01 provides only how a triage outcome lands."""

from dataclasses import replace

from morning_brew.domain import Decision, HoldKind, TriageOutcome, Verdict


def apply_outcome(d: Decision, o: TriageOutcome) -> Decision:
    """Resolve a GREY decision from a triage outcome; anything else is unchanged.

    No model may turn a HOLD, SKIP, DEFER or REPORT into an upgrade.
    """
    if d.verdict is not Verdict.GREY:
        return d
    if o.kind == "upgrade":
        return replace(d, verdict=Verdict.ELIGIBLE, reason=o.reason, hold_kind=None)
    if o.kind == "hold":
        return replace(
            d, verdict=Verdict.HOLD, reason=o.reason, hold_kind=HoldKind.DECIDED
        )
    return replace(
        d, verdict=Verdict.HOLD, reason=o.reason, hold_kind=HoldKind.TRANSIENT
    )
