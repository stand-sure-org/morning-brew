"""One run of the pipeline (SPEC FR-005). T01: the happy path.

Order: lock, update, snapshot, reconcile, deferred cleanup, rules, components,
triage of GREY members, components again, apply, verify snapshot, audit,
status, notify, stamp. Triage always completes before the first upgrade.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from morning_brew.config import Config
from morning_brew.domain import (
    EXIT_OK,
    AuditEvent,
    Candidate,
    ComponentPlan,
    Decision,
    Hold,
    HoldKind,
    RuleContext,
    RunResult,
    Snapshot,
    Stamp,
    Summary,
    TriageOutcome,
    Verdict,
    request_from,
)
from morning_brew.ports import Ports
from morning_brew.rules import apply_outcome

log = logging.getLogger(__name__)


@dataclass
class _Applied:
    upgraded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def run_once(ports: Ports, config: Config, *, dry_run: bool = False) -> RunResult:
    with ports.lock.acquire():
        now = ports.clock()
        run_id = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        prev_stamp = ports.stamp.read()
        current_seq = prev_stamp.full_runs if prev_stamp else 0

        ports.brew.update()
        snapshot = ports.brew.snapshot()
        candidates = {c.full_name: c for c in snapshot.candidates}
        live = ports.live.live_set(snapshot)
        ports.reconcile(snapshot, ports.holds)
        _deferred_cleanup(ports, config, current_seq, live)

        for c in snapshot.candidates:
            ports.seen.record_first_seen(c.full_name, c.current, now)
        ctx = RuleContext(
            live=live,
            trusted_taps=config.trusted_taps,
            self_deps=ports.self_deps,
            now=now,
            min_age_days=config.min_age_days,
            first_seen=ports.seen.first_seen,
            caveat_hash=ports.seen.caveat_hash,
        )
        decisions = ports.rules(snapshot, ctx)
        plans = ports.components(snapshot, decisions)

        outcomes = _triage_grey(ports, plans, decisions, candidates)
        if outcomes:
            decisions = [
                apply_outcome(d, outcomes[d.full_name])
                if d.full_name in outcomes
                else d
                for d in decisions
            ]
            plans = ports.components(snapshot, decisions)

        _store_holds(ports, decisions, candidates, now)
        applied = _apply(ports, plans)
        if applied.upgraded:
            ports.seen.record_upgrade(current_seq + 1, applied.upgraded)
        ports.brew.snapshot()  # verify by re-snapshot (comparison lands in T02)

        for event in _audit_events(
            run_id, now, snapshot, decisions, plans, outcomes, applied
        ):
            ports.audit.record(event)

        summary = _summary(run_id, now, config, ports, decisions, outcomes, applied)
        ports.status.write(summary)
        ports.notifier.notify(summary, urgent=summary.dead_man)
        ports.stamp.write(_next_stamp(prev_stamp, summary, now))
        return RunResult(exit_code=EXIT_OK, summary=summary)


def _deferred_cleanup(
    ports: Ports, config: Config, current_seq: int, live: frozenset[str]
) -> None:
    due = ports.seen.due_for_cleanup(current_seq, config.cleanup_runs)
    ports.brew.cleanup([name for name in due if name not in live])


def _triage_grey(
    ports: Ports,
    plans: Sequence[ComponentPlan],
    decisions: Sequence[Decision],
    candidates: Mapping[str, Candidate],
) -> dict[str, TriageOutcome]:
    by_name = {d.full_name: d for d in decisions}
    outcomes: dict[str, TriageOutcome] = {}
    for plan in plans:
        if plan.status is not Verdict.GREY or plan.blocked_by is not None:
            continue
        for member in plan.members:
            decision = by_name.get(member)
            candidate = candidates.get(member)
            if decision is None or candidate is None:
                continue
            if decision.verdict is not Verdict.GREY:
                continue
            outcomes[member] = ports.triage.triage(request_from(candidate))
    return outcomes


def _store_holds(
    ports: Ports,
    decisions: Sequence[Decision],
    candidates: Mapping[str, Candidate],
    now: datetime,
) -> None:
    for d in decisions:
        if d.verdict is not Verdict.HOLD:
            continue
        candidate = candidates.get(d.full_name)
        version = candidate.current if candidate else ""
        existing = ports.holds.get(d.full_name)
        ports.holds.put(
            Hold(
                name=d.full_name,
                kind=d.hold_kind or HoldKind.DECIDED,
                held_at_version=version,
                reason=d.reason,
                first_held=existing.first_held if existing else now,
            )
        )


def _apply(ports: Ports, plans: Sequence[ComponentPlan]) -> _Applied:
    applied = _Applied()
    for plan in plans:
        if plan.status is not Verdict.ELIGIBLE:
            continue
        result = ports.brew.upgrade(plan.members)
        target = applied.upgraded if result.ok else applied.failed
        target.extend(plan.members)
    return applied


def _component_id(plan: ComponentPlan | None) -> str | None:
    if plan is None or len(plan.members) < 2:
        return None
    return ",".join(sorted(plan.members))


def _audit_events(
    run_id: str,
    now: datetime,
    snapshot: Snapshot,
    decisions: Sequence[Decision],
    plans: Sequence[ComponentPlan],
    outcomes: Mapping[str, TriageOutcome],
    applied: _Applied,
) -> list[AuditEvent]:
    plan_of = {m: p for p in plans for m in p.members}
    events: list[AuditEvent] = []
    for d in decisions:
        plan = plan_of.get(d.full_name)
        outcome = outcomes.get(d.full_name)
        verdict = plan.status if plan else d.verdict
        action = None
        if d.full_name in applied.upgraded:
            action = "upgraded"
        elif d.full_name in applied.failed:
            action = "failed"
        events.append(
            AuditEvent(
                run_id=run_id,
                ts=now,
                brew_version=snapshot.brew_version,
                stage="decide",
                package=d.full_name,
                rule_id="chain" if outcome else d.rule_id,
                verdict=str(verdict),
                action_result=action,
                models=((outcome.stage, None),) if outcome else (),
                reasoning=outcome.reasoning if outcome else None,
                component=_component_id(plan),
            )
        )
    return events


def _summary(
    run_id: str,
    now: datetime,
    config: Config,
    ports: Ports,
    decisions: Sequence[Decision],
    outcomes: Mapping[str, TriageOutcome],
    applied: _Applied,
) -> Summary:
    decided = [h for h in ports.holds.list_all() if h.kind is HoldKind.DECIDED]
    oldest = max(((now - h.first_held).days for h in decided), default=None)
    transient = sum(1 for d in decisions if d.hold_kind is HoldKind.TRANSIENT)
    return Summary(
        run_id=run_id,
        quality="degraded" if transient else "full",
        upgraded=tuple(applied.upgraded),
        failed=tuple(applied.failed),
        decided_holds=len(decided),
        oldest_decided_hold_days=oldest,
        blocked_by_dep=0,
        transient=transient,
        self_dep_commands=(),
        disagreements=tuple(n for n, o in outcomes.items() if o.disagreement),
        live_old_binary=(),
        anomalies=(),
        dead_man=False,
        degraded_reasons=(),
    )


def _next_stamp(prev: Stamp | None, summary: Summary, now: datetime) -> Stamp:
    prev_runs = prev.full_runs if prev else 0
    if summary.quality == "full":
        return Stamp(
            last_full_success=now,
            last_attempt=now,
            quality="full",
            full_runs=prev_runs + 1,
        )
    return Stamp(
        last_full_success=prev.last_full_success if prev else None,
        last_attempt=now,
        quality="degraded",
        full_runs=prev_runs,
    )
