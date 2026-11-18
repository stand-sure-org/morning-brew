"""One run of the pipeline (SPEC FR-005). T01: the happy path.

Order: lock, update, snapshot, reconcile, deferred cleanup, rules, existing
holds applied [R14], components, triage of GREY members, components again,
apply, audit, verify snapshot, status, notify, weekly digest, stamp. Triage
always completes before the first upgrade. Past the gate, any failure is a
recorded degraded attempt (exit 1), never a silent crash [I1].
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from morning_brew.aging import digest_due, hold_aging, render_digest, stale_holds
from morning_brew.caveats import caveat_hash
from morning_brew.config import Config
from morning_brew.domain import (
    EXIT_ERROR,
    EXIT_OK,
    EXIT_TEMPFAIL,
    AuditEvent,
    BrewBusyError,
    BrewParseError,
    BrewUpdateError,
    Candidate,
    ComponentPlan,
    Decision,
    Hold,
    HoldKind,
    InvalidPackageName,
    LockHeld,
    RuleContext,
    RunResult,
    Snapshot,
    Stamp,
    StateUnwritable,
    Summary,
    TriageOutcome,
    Verdict,
    request_from,
)
from morning_brew.ports import Ports
from morning_brew.rules import apply_outcome
from morning_brew.schedule import dead_man, should_run
from morning_brew.store.holds import is_suppressed, needs_retriage

log = logging.getLogger(__name__)


@dataclass
class _Applied:
    upgraded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    busy: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)


BUDGET_REASON = "run budget exceeded; retry next run"
SKIP_REASON = "skipped by owner for this version"


AlarmsFn = Callable[[], Sequence[str]]


def run_once(
    ports: Ports,
    config: Config,
    *,
    dry_run: bool = False,
    alarms: AlarmsFn | None = None,
) -> RunResult:
    """One run. ``alarms`` is read after triage and its lines become urgent
    anomalies of this run's single summary (status, notification, stamp)."""
    try:
        with ports.lock.acquire():
            return _locked_run(ports, config, dry_run=dry_run, alarms=alarms)
    except LockHeld:
        log.warning("another morning-brew run holds the lock; exiting")
        return RunResult(exit_code=EXIT_TEMPFAIL, summary=None)


def _locked_run(
    ports: Ports, config: Config, *, dry_run: bool, alarms: AlarmsFn | None
) -> RunResult:
    now = ports.clock()
    run_id = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    prev_stamp = ports.stamp.read()
    stale = dead_man(now, prev_stamp, config.dead_man_hours)

    # A dry run is an explicit request: it bypasses the gate [R2].
    if not dry_run:
        gate = should_run(now, prev_stamp, config.earliest_hour)
        if not gate.run:
            log.info("gated no-op: %s", gate.reason)
            if stale:
                _notify(ports, _gated_summary(run_id, stale))
            return RunResult(exit_code=EXIT_OK, summary=None)

        try:
            ports.holds.ensure_writable()
        except StateUnwritable:
            log.error("state directory is not writable; aborting before update")
            # [I1] still an attempt: the stamp may be writable even when
            # holds/ is not, and the dead-man must be able to trip
            _record_failed_attempt(ports, run_id, now, prev_stamp, config)
            return RunResult(exit_code=EXIT_ERROR, summary=None)
        try:
            ports.brew.update()
        except BrewUpdateError:
            log.exception("brew update failed; aborting before any mutation")
            _record_failed_attempt(ports, run_id, now, prev_stamp, config)
            return RunResult(exit_code=EXIT_TEMPFAIL, summary=None)
        except Exception:
            log.exception("brew update raised unexpectedly; aborting")
            _record_failed_attempt(ports, run_id, now, prev_stamp, config)
            return RunResult(exit_code=EXIT_ERROR, summary=None)
    try:
        return _run_body(
            ports,
            config,
            now=now,
            run_id=run_id,
            prev_stamp=prev_stamp,
            stale=stale,
            dry_run=dry_run,
            alarms=alarms,
        )
    except Exception as exc:
        if dry_run:  # a dry run records nothing; the CLI reports the error
            raise
        # [I1] Past the gate every failure is a recorded, degraded attempt so
        # the dead-man can trip instead of the run vanishing silently.
        if isinstance(exc, _EXPECTED_ERRORS):
            log.error(
                "run aborted after the gate: %s: %s", type(exc).__name__, _line(exc)
            )
        else:
            log.exception("run aborted after the gate")
        _record_failed_attempt(ports, run_id, now, prev_stamp, config)
        return RunResult(exit_code=EXIT_ERROR, summary=None)


_EXPECTED_ERRORS = (
    BrewParseError,
    BrewBusyError,
    InvalidPackageName,
    StateUnwritable,
    OSError,
)


def _line(exc: BaseException) -> str:
    lines = str(exc).strip().splitlines()
    return lines[0] if lines else type(exc).__name__


def _run_body(
    ports: Ports,
    config: Config,
    *,
    now: datetime,
    run_id: str,
    prev_stamp: Stamp | None,
    stale: bool,
    dry_run: bool,
    alarms: AlarmsFn | None,
) -> RunResult:
    current_seq = prev_stamp.full_runs if prev_stamp else 0
    snapshot = ports.brew.snapshot()
    candidates = {c.full_name: c for c in snapshot.candidates}
    unjoinable = frozenset(snapshot.unjoinable)
    live = ports.live.live_set(snapshot)
    pin_released: list[str] = []
    if not dry_run:
        pin_released = ports.reconcile(snapshot, ports.holds)
        _release_not_outdated(ports, candidates, unjoinable)
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
    decisions = _apply_existing_holds(ports, ports.rules(snapshot, ctx), candidates)
    plans = ports.components(snapshot, decisions)

    outcomes = _triage_grey(ports, config, now, plans, decisions, candidates)
    if outcomes:
        decisions = [
            apply_outcome(d, outcomes[d.full_name]) if d.full_name in outcomes else d
            for d in decisions
        ]
        plans = ports.components(snapshot, decisions)
    extra = tuple(alarms()) if alarms is not None else ()

    if dry_run:
        idle = _Applied(anomalies=list(extra))
        for event in _audit_events(
            run_id, now, snapshot, decisions, plans, outcomes, idle, "dry_run"
        ):
            ports.audit.record(event)
        summary = _summary(
            run_id,
            now,
            config,
            ports,
            decisions,
            plans,
            outcomes,
            idle,
            stale,
            unjoinable,
        )
        return RunResult(exit_code=EXIT_OK, summary=summary)

    _store_holds(ports, decisions, plans, outcomes, candidates, now)
    _store_unjoinable(ports, unjoinable, now)
    applied = _apply(ports, plans)
    _store_busy(ports, applied, candidates, now)
    for name in applied.upgraded:  # no longer outdated
        ports.holds.release(name)
    _release_stale_transients(ports, decisions, applied, unjoinable)
    _record_caveat_hashes(ports, applied, candidates)
    if applied.upgraded:
        ports.seen.record_upgrade(current_seq + 1, applied.upgraded)

    # [I1] Audit what was done before re-reading brew: a failed verify
    # snapshot must cost an anomaly, never the record of an upgrade.
    for event in _reconcile_events(run_id, now, snapshot, pin_released):
        ports.audit.record(event)
    for event in _audit_events(
        run_id, now, snapshot, decisions, plans, outcomes, applied, "decide"
    ):
        ports.audit.record(event)
    applied.anomalies.extend(_verify_after(ports, snapshot, applied))
    applied.anomalies.extend(extra)

    summary = _summary(
        run_id,
        now,
        config,
        ports,
        decisions,
        plans,
        outcomes,
        applied,
        stale,
        unjoinable,
    )
    try:
        ports.status.write(summary)
    except Exception as exc:  # the run itself succeeded; keep its exit code
        log.error("status not written: %s", type(exc).__name__)
    _notify(ports, summary)
    _send_digest(ports, config, now)
    ports.stamp.write(_next_stamp(prev_stamp, summary, now))
    return RunResult(exit_code=EXIT_OK, summary=summary)


def _verify_after(ports: Ports, pre: Snapshot, applied: _Applied) -> list[str]:
    try:
        post = ports.brew.snapshot()
    except Exception as exc:
        log.error("post-apply verify snapshot failed: %s", type(exc).__name__)
        return [f"post-apply verify failed: {type(exc).__name__}"]
    return _verify(pre, post, applied)


def _send_digest(ports: Ports, config: Config, now: datetime) -> None:
    """[R57] Weekly, through the normal channel; counts and names only."""
    if ports.digest is None:
        return
    try:
        if not digest_due(ports.digest.last_sent(), now):
            return
        holds = ports.holds.list_all()
        text = render_digest(
            hold_aging(holds, now), stale_holds(holds, now, config.stale_hold_days)
        )
        if ports.digest.send(text):
            ports.digest.record_sent(now)
    except Exception as exc:  # a digest must never fail the run
        log.warning("weekly digest not sent: %s", type(exc).__name__)


def _record_failed_attempt(
    ports: Ports, run_id: str, now: datetime, prev: Stamp | None, config: Config
) -> None:
    """[R17] A failed update is an attempt: stamp it so the dead-man can trip."""
    stamp = Stamp(
        last_full_success=prev.last_full_success if prev else None,
        last_attempt=now,
        quality="degraded",
        full_runs=prev.full_runs if prev else 0,
        first_attempt=(prev.first_attempt if prev else None) or now,
    )
    try:
        ports.stamp.write(stamp)
    except OSError:
        log.exception("could not record the failed attempt")
        return
    if dead_man(now, stamp, config.dead_man_hours):
        _notify(ports, _gated_summary(run_id, True))


def _gated_summary(run_id: str, stale: bool) -> Summary:
    return Summary(
        run_id=run_id,
        quality="degraded",
        upgraded=(),
        failed=(),
        decided_holds=0,
        oldest_decided_hold_days=None,
        blocked_by_dep=0,
        transient=0,
        self_dep_commands=(),
        disagreements=(),
        live_old_binary=(),
        anomalies=(),
        dead_man=stale,
        degraded_reasons=("dead-man: no full success within the window",),
    )


def _notify(ports: Ports, summary: Summary) -> None:
    try:
        ports.notifier.notify(
            summary, urgent=summary.dead_man or bool(summary.anomalies)
        )
    except Exception:  # a broken notifier must never fail the run
        log.exception("notifier failed; continuing")


def _deferred_cleanup(
    ports: Ports, config: Config, current_seq: int, live: frozenset[str]
) -> None:
    due = ports.seen.due_for_cleanup(current_seq, config.cleanup_runs)
    ports.brew.cleanup([name for name in due if name not in live])


def _release_not_outdated(
    ports: Ports, candidates: Mapping[str, Candidate], unjoinable: frozenset[str]
) -> None:
    """[R14] A hold whose package is no longer outdated has nothing to hold.

    [I6] An unjoinable name is still outdated (brew listed it), only its info
    is missing; its hold, including an owner skip, is kept.
    """
    for hold in ports.holds.list_all():
        if hold.name not in candidates and hold.name not in unjoinable:
            log.info("releasing hold for %s: no longer outdated", hold.name)
            ports.holds.release(hold.name)


def _release_stale_transients(
    ports: Ports,
    decisions: Sequence[Decision],
    applied: _Applied,
    unjoinable: frozenset[str],
) -> None:
    """[FR-061] A transient row lives one run: retried, it is held, busy or gone.

    DECIDED rows are kept (their reason and first_held must survive a DEFER).
    """
    held = {d.full_name for d in decisions if d.verdict is Verdict.HOLD}
    busy = set(applied.busy)
    for hold in ports.holds.list_all():
        if hold.kind is not HoldKind.TRANSIENT:
            continue
        if hold.name in held or hold.name in busy or hold.name in unjoinable:
            continue
        ports.holds.release(hold.name)


def _record_caveat_hashes(
    ports: Ports, applied: _Applied, candidates: Mapping[str, Candidate]
) -> None:
    """[R32] After a successful upgrade the caveat seen is no longer news."""
    for name in applied.upgraded:
        candidate = candidates.get(name)
        h = caveat_hash(candidate.caveats) if candidate is not None else None
        if h is not None:
            ports.seen.record_caveat_hash(name, h)


def _apply_existing_holds(
    ports: Ports, decisions: Sequence[Decision], candidates: Mapping[str, Candidate]
) -> list[Decision]:
    """[R14] Overlay the hold ledger on the rule decisions, before components.

    An owner skip for this version always wins (never auto-upgraded); a decided
    hold for an unchanged version keeps a GREY member held without a model
    call; transient holds change nothing, so they are retried every run.
    """
    out: list[Decision] = []
    for d in decisions:
        candidate = candidates.get(d.full_name)
        hold = ports.holds.get(d.full_name) if candidate is not None else None
        if hold is None or candidate is None:
            out.append(d)
            continue
        if is_suppressed(hold, candidate.current):
            out.append(
                Decision(
                    d.full_name, Verdict.HOLD, "skip", SKIP_REASON, HoldKind.DECIDED
                )
            )
        elif (
            hold.kind is HoldKind.DECIDED
            and not needs_retriage(hold, candidate.current)
            and d.verdict is Verdict.GREY
        ):
            out.append(
                Decision(
                    d.full_name, Verdict.HOLD, "held", hold.reason, HoldKind.DECIDED
                )
            )
        else:
            out.append(d)
    return out


def _triage_grey(
    ports: Ports,
    config: Config,
    started: datetime,
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
            if ports.clock() - started > timedelta(minutes=config.run_budget_minutes):
                outcomes[member] = TriageOutcome("transient", BUDGET_REASON, "budget")
                continue
            outcomes[member] = ports.triage.triage(request_from(candidate))
    return outcomes


def _store_holds(
    ports: Ports,
    decisions: Sequence[Decision],
    plans: Sequence[ComponentPlan],
    outcomes: Mapping[str, TriageOutcome],
    candidates: Mapping[str, Candidate],
    now: datetime,
) -> None:
    plan_of = {m: p for p in plans for m in p.members}
    for d in decisions:
        if d.verdict is not Verdict.HOLD:
            continue
        candidate = candidates.get(d.full_name)
        version = candidate.current if candidate else ""
        kind = d.hold_kind or HoldKind.DECIDED
        plan = plan_of.get(d.full_name)
        blocked_by = plan.blocked_by if plan else None
        outcome = outcomes.get(d.full_name)
        existing = ports.holds.get(d.full_name)
        if existing is not None:
            # [FR-061] transient time never counts toward decided age
            restarted = existing.kind is HoldKind.TRANSIENT and kind is HoldKind.DECIDED
            hold = replace(
                existing,
                kind=kind,
                held_at_version=version,
                reason=d.reason,
                blocked_by=blocked_by,
                first_held=now if restarted else existing.first_held,
            )
            if outcome is not None:
                hold = replace(hold, model_reasoning=outcome.reasoning)
        else:
            hold = Hold(
                name=d.full_name,
                kind=kind,
                held_at_version=version,
                reason=d.reason,
                first_held=now,
                blocked_by=blocked_by,
                model_reasoning=outcome.reasoning if outcome else None,
            )
        ports.holds.put(hold)


UNJOINABLE_REASON = "unjoinable: brew listed it outdated without info; retry"


def _store_unjoinable(ports: Ports, names: frozenset[str], now: datetime) -> None:
    """[I6] An outdated name brew gave no info for is held transiently.

    An existing row (a decided hold, an owner skip, last run's transient) is
    never overwritten: its version, first_held and skip must survive.
    """
    for name in sorted(names):
        if ports.holds.get(name) is not None:
            continue
        ports.holds.put(
            Hold(
                name=name,
                kind=HoldKind.TRANSIENT,
                held_at_version="",
                reason=UNJOINABLE_REASON,
                first_held=now,
            )
        )


def _store_busy(
    ports: Ports,
    applied: _Applied,
    candidates: Mapping[str, Candidate],
    now: datetime,
) -> None:
    for name in applied.busy:
        candidate = candidates.get(name)
        version = candidate.current if candidate else ""
        existing = ports.holds.get(name)
        if existing is not None:
            hold = replace(
                existing,
                kind=HoldKind.TRANSIENT,
                held_at_version=version,
                reason="brew busy; retry next run",
            )
        else:
            hold = Hold(
                name=name,
                kind=HoldKind.TRANSIENT,
                held_at_version=version,
                reason="brew busy; retry next run",
                first_held=now,
            )
        ports.holds.put(hold)


def _apply(ports: Ports, plans: Sequence[ComponentPlan]) -> _Applied:
    applied = _Applied()
    for plan in plans:
        if plan.status is not Verdict.ELIGIBLE:
            continue
        expected = frozenset(plan.members)
        closure = ports.brew.dry_run_closure(plan.members)
        if closure != expected:
            extra = ",".join(sorted(closure - expected))
            missing = ",".join(sorted(expected - closure))
            applied.excluded.extend(plan.members)
            applied.anomalies.append(
                f"closure mismatch for {','.join(plan.members)}: "
                f"extra [{extra}] missing [{missing}]"
            )
            continue
        try:
            result = ports.brew.upgrade(plan.members)
        except BrewBusyError:
            log.warning("brew busy; %s deferred to next run", plan.members)
            applied.busy.extend(plan.members)
            continue
        target = applied.upgraded if result.ok else applied.failed
        target.extend(plan.members)
    return applied


def _verify(pre: Snapshot, post: Snapshot, applied: _Applied) -> list[str]:
    """Anomaly: a package we did not touch changed installed version or vanished."""
    touched = set(applied.upgraded) | set(applied.failed)
    after = {c.full_name: c for c in post.candidates}
    anomalies: list[str] = []
    for c in pre.candidates:
        if c.full_name in touched:
            continue
        now_c = after.get(c.full_name)
        if now_c is None or now_c.installed != c.installed:
            anomalies.append(f"{c.full_name} changed during run without being upgraded")
    return anomalies


def _component_id(plan: ComponentPlan | None) -> str | None:
    if plan is None or len(plan.members) < 2:
        return None
    return ",".join(sorted(plan.members))


def _hold_kind(verdict: Verdict, d: Decision) -> str | None:
    if verdict is not Verdict.HOLD:
        return None
    return (d.hold_kind or HoldKind.DECIDED).value


def _reconcile_events(
    run_id: str, now: datetime, snapshot: Snapshot, released: Sequence[str]
) -> list[AuditEvent]:
    return [
        AuditEvent(
            run_id=run_id,
            ts=now,
            brew_version=snapshot.brew_version,
            stage="reconcile",
            package=name,
            rule_id="R02",
            verdict=str(Verdict.SKIP),
            action_result="released",
        )
        for name in released
    ]


def _audit_events(
    run_id: str,
    now: datetime,
    snapshot: Snapshot,
    decisions: Sequence[Decision],
    plans: Sequence[ComponentPlan],
    outcomes: Mapping[str, TriageOutcome],
    applied: _Applied,
    stage: str,
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
        elif d.full_name in applied.busy:
            action = "transient"
        elif d.full_name in applied.excluded:
            action = "excluded"
        events.append(
            AuditEvent(
                run_id=run_id,
                ts=now,
                brew_version=snapshot.brew_version,
                stage=stage,
                package=d.full_name,
                rule_id="chain" if outcome else d.rule_id,
                verdict=str(verdict),
                action_result=action,
                models=((outcome.stage, None),) if outcome else (),
                booleans=_triage_booleans(outcome) if outcome else None,
                reasoning=outcome.reasoning if outcome else None,
                component=_component_id(plan),
                hold_kind=_hold_kind(verdict, d),
            )
        )
    return events


def _triage_booleans(o: TriageOutcome) -> dict[str, bool]:
    """What the chain would have done, for the shadow report (T24).

    A shadow would-upgrade and a Claude outage are both transient holds;
    only these flags tell them apart in the audit log.
    """
    return {
        "would_upgrade": "upgrade" in (o.kind, o.would_be),
        "would_hold": o.kind == "hold",
        "disagreement": o.disagreement,
        "local_flagged": o.local_flagged,
    }


def _summary(
    run_id: str,
    now: datetime,
    config: Config,
    ports: Ports,
    decisions: Sequence[Decision],
    plans: Sequence[ComponentPlan],
    outcomes: Mapping[str, TriageOutcome],
    applied: _Applied,
    stale: bool,
    unjoinable: frozenset[str] = frozenset(),
) -> Summary:
    holds = ports.holds.list_all()
    aging = hold_aging(holds, now)
    stale_names = [h.name for h in stale_holds(holds, now, config.stale_hold_days)]
    anomalies = list(applied.anomalies)
    if stale_names:  # an anomaly makes the notification urgent
        anomalies.append(
            f"stale holds (over {config.stale_hold_days} days): "
            + ", ".join(stale_names)
        )
    # Run quality follows this run's transients, not ledger rows. [R55] A
    # shadow outcome (would_be set) is the chain working as designed: it is
    # retried daily and does not degrade the run.
    shadow = {n for n, o in outcomes.items() if o.would_be is not None}
    transient = sum(
        1
        for d in decisions
        if d.hold_kind is HoldKind.TRANSIENT and d.full_name not in shadow
    ) + len(applied.busy)
    reasons: list[str] = []
    if transient:
        reasons.append("transient holds")
    if unjoinable:  # [I6]
        reasons.append("unjoinable")
    self_dep_commands = tuple(
        "brew upgrade -- " + " ".join(sorted(p.members))
        for p in plans
        if p.status is Verdict.DEFER and ports.self_deps.intersection(p.members)
    )
    return Summary(
        run_id=run_id,
        quality="degraded" if reasons else "full",
        upgraded=tuple(applied.upgraded),
        failed=tuple(applied.failed),
        decided_holds=aging.decided_count,
        oldest_decided_hold_days=aging.oldest_decided_days,
        blocked_by_dep=aging.blocked_by_dep_count,
        transient=transient + len(unjoinable),
        self_dep_commands=self_dep_commands,
        disagreements=tuple(n for n, o in outcomes.items() if o.disagreement),
        live_old_binary=(),
        anomalies=tuple(anomalies),
        dead_man=stale,
        degraded_reasons=tuple(reasons),
    )


def _next_stamp(prev: Stamp | None, summary: Summary, now: datetime) -> Stamp:
    prev_runs = prev.full_runs if prev else 0
    first = (prev.first_attempt if prev else None) or now
    if summary.quality == "full":
        return Stamp(
            last_full_success=now,
            last_attempt=now,
            quality="full",
            full_runs=prev_runs + 1,
            first_attempt=first,
        )
    return Stamp(
        last_full_success=prev.last_full_success if prev else None,
        last_attempt=now,
        quality="degraded",
        full_runs=prev_runs,
        first_attempt=first,
    )
