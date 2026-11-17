"""Deterministic rules: first match wins, rules always beat the model."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta

from morning_brew.caveats import caveat_hash
from morning_brew.components import validate_name
from morning_brew.domain import (
    Bump,
    Candidate,
    Decision,
    HoldKind,
    Kind,
    RuleContext,
    Snapshot,
    TriageOutcome,
    Verdict,
)

CORE_TAP = "homebrew/core"
DEPENDENTS_HOLD = 10


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


@dataclass(frozen=True)
class Rule:
    id: str
    applies: Callable[[Candidate, RuleContext], bool]
    verdict: Verdict
    hold_kind: HoldKind | None
    reason: str  # str.format template over name, tap, bump, dependents


def _too_young(c: Candidate, ctx: RuleContext) -> bool:
    seen = ctx.first_seen(c.full_name, c.current)
    return seen is None or ctx.now - seen < timedelta(days=ctx.min_age_days)


def _caveat_changed(c: Candidate, ctx: RuleContext) -> bool:
    h = caveat_hash(c.caveats)
    return h is not None and h != ctx.caveat_hash(c.full_name)


def _risky(c: Candidate, ctx: RuleContext) -> bool:
    return (
        c.bump in (Bump.MAJOR, Bump.UNPARSEABLE)
        or c.deprecated
        or (not c.is_leaf and c.dependents >= DEPENDENTS_HOLD)
    )


RULES: tuple[Rule, ...] = (
    Rule(
        "R01",
        lambda c, ctx: not validate_name(c.full_name),
        Verdict.HOLD,
        HoldKind.TRANSIENT,
        "R01 invalid package name {name!r}",
    ),
    Rule("R02", lambda c, ctx: c.pinned, Verdict.SKIP, None, "R02 pinned {name}"),
    Rule(
        "R03",
        lambda c, ctx: c.kind is Kind.CASK,
        Verdict.REPORT,
        None,
        "R03 cask {name} is report-only",
    ),
    Rule(
        "R04",
        lambda c, ctx: c.disabled,
        Verdict.HOLD,
        HoldKind.DECIDED,
        "R04 disabled {name}",
    ),
    Rule(
        "R05",
        lambda c, ctx: c.full_name in ctx.self_deps,
        Verdict.REPORT,
        None,
        "R05 {name} is a dependency of this tool",
    ),
    Rule(
        "R06",
        lambda c, ctx: c.tap != CORE_TAP and c.tap not in ctx.trusted_taps,
        Verdict.HOLD,
        HoldKind.DECIDED,
        "R06 untrusted tap {tap}",
    ),
    Rule(
        "R07",
        lambda c, ctx: not c.has_arm64_bottle,
        Verdict.HOLD,
        HoldKind.DECIDED,
        "R07 no arm64 bottle for {name}",
    ),
    Rule(
        "R08",
        lambda c, ctx: c.full_name in ctx.live,
        Verdict.HOLD,
        HoldKind.DECIDED,
        "R08 {name} is a live service",
    ),
    Rule(
        "R09",
        _risky,
        Verdict.HOLD,
        HoldKind.DECIDED,
        "R09 risky upgrade {name} (bump {bump}, {dependents} dependents)",
    ),
    Rule(
        "R10",
        _too_young,
        Verdict.DEFER,
        None,
        "R10 {name} not yet soaked",
    ),
    Rule(
        "R11",
        lambda c, ctx: c.bump is Bump.ZERO_MAJOR_MINOR or _caveat_changed(c, ctx),
        Verdict.GREY,
        None,
        "R11 needs triage: {name} (0.x minor bump or caveat change)",
    ),
    Rule("R12", lambda c, ctx: True, Verdict.ELIGIBLE, None, "R12 safe: {name}"),
)


def evaluate(c: Candidate, ctx: RuleContext) -> Decision:
    for rule in RULES:
        if rule.applies(c, ctx):
            reason = rule.reason.format(
                name=c.full_name, tap=c.tap, bump=c.bump.value, dependents=c.dependents
            )
            return Decision(c.full_name, rule.verdict, rule.id, reason, rule.hold_kind)
    raise AssertionError("R12 always applies")  # pragma: no cover


def evaluate_all(snapshot: Snapshot, ctx: RuleContext) -> list[Decision]:
    return [evaluate(c, ctx) for c in snapshot.candidates]
