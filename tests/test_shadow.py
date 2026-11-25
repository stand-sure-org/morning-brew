"""Shadow report over the audit log (T24) and the audit fields it reads."""

from datetime import UTC, datetime, timedelta

from builders import make_candidate, make_snapshot
from fakes import (
    FakeAudit,
    FakeBrew,
    FakeHolds,
    FakeLive,
    FakeLock,
    FakeNotifier,
    FakeSeen,
    FakeStamp,
    FakeStatus,
    FakeTriage,
    grouping_components,
    recording_reconcile,
    recording_rules,
)
from morning_brew.config import Config
from morning_brew.domain import AuditEvent, Decision, TriageOutcome, Verdict
from morning_brew.orchestrator import run_once
from morning_brew.ports import Ports
from morning_brew.shadow import ShadowReport, shadow_report
from morning_brew.store.audit import JsonlAudit

T0 = datetime(2026, 11, 17, 8, 0, tzinfo=UTC)


def chain_event(
    run: int,
    package: str,
    *,
    stage: str = "decide",
    model_stage: str = "claude",
    verdict: str = "hold",
    hold_kind: str | None = "transient",
    would_upgrade: bool = False,
    would_hold: bool = False,
    disagreement: bool = False,
) -> AuditEvent:
    return AuditEvent(
        run_id=f"run{run:02d}",
        ts=T0 + timedelta(hours=run),
        brew_version="Homebrew 5.0.0",
        stage=stage,
        package=package,
        rule_id="chain",
        verdict=verdict,
        models=((model_stage, None),),
        booleans={
            "would_upgrade": would_upgrade,
            "would_hold": would_hold,
            "disagreement": disagreement,
            "local_flagged": disagreement,
        },
        hold_kind=hold_kind,
    )


def rule_event(run: int, package: str) -> AuditEvent:
    return AuditEvent(
        run_id=f"run{run:02d}",
        ts=T0 + timedelta(hours=run),
        brew_version="Homebrew 5.0.0",
        stage="decide",
        package=package,
        rule_id="R12",
        verdict="eligible",
    )


def synthetic_10_runs() -> list[AuditEvent]:
    events: list[AuditEvent] = []
    for run in range(10):
        events.append(rule_event(run, "plain"))  # every run counts, triaged or not
    # 7 shadow would-upgrades, 2 of them flagged by the local model
    for run in range(7):
        events.append(
            chain_event(run, "grey", would_upgrade=True, disagreement=run in (1, 4))
        )
    # 2 Claude shadow holds and 1 deterministic suspicious hold (would_be=None)
    events.append(chain_event(7, "grey", hold_kind="decided", would_hold=True))
    events.append(chain_event(8, "grey", hold_kind="decided", would_hold=True))
    events.append(chain_event(9, "grey", model_stage="suspicious", hold_kind="decided"))
    return events


def test_shadow_report_counts():
    assert shadow_report(synthetic_10_runs()) == ShadowReport(
        runs=10, would_upgrade=7, would_hold=3, disagreements=2
    )


def test_shadow_report_counts_suspicious_hold_as_hold():
    # T18: a suspicious hold carries would_be=None; it is a hold, not an upgrade.
    event = chain_event(0, "x", model_stage="suspicious", hold_kind="decided")
    assert shadow_report([event]) == ShadowReport(1, 0, 1, 0)


def test_shadow_report_counts_outage_transient_as_neither():
    event = chain_event(0, "x")  # Claude unavailable: transient, no would-be
    assert shadow_report([event]) == ShadowReport(1, 0, 0, 0)


def test_shadow_report_counts_only_decide_stage():
    events = [
        chain_event(0, "x", stage="dry_run", would_upgrade=True),
        AuditEvent(
            run_id="eval-1",
            ts=T0,
            brew_version="eval",
            stage="eval",
            package="golden/r1",
            verdict="upgrade",
        ),
        AuditEvent(run_id="rv", ts=T0, brew_version="", stage="review", package="x"),
    ]
    assert shadow_report(events) == ShadowReport(0, 0, 0, 0)


def test_shadow_report_empty():
    assert shadow_report([]) == ShadowReport(0, 0, 0, 0)


def test_jsonl_audit_all_events_round_trip(tmp_path):
    audit = JsonlAudit(tmp_path / "audit.jsonl")
    for event in synthetic_10_runs():
        audit.record(event)
    assert shadow_report(audit.all_events()) == ShadowReport(10, 7, 3, 2)


def _ports(outcome: TriageOutcome) -> Ports:
    calls: list[str] = []
    snap = make_snapshot(make_candidate("grype", caveats="restart after upgrade"))
    return Ports(
        brew=FakeBrew(calls, [snap]),
        live=FakeLive(),
        rules=recording_rules(
            calls, [Decision("grype", Verdict.GREY, "R11", "caveat changed")]
        ),
        components=grouping_components(calls, [["grype"]]),
        reconcile=recording_reconcile(calls),
        triage=FakeTriage(calls, {"grype": outcome}),
        holds=FakeHolds(),
        seen=FakeSeen(),
        audit=FakeAudit(calls),
        notifier=FakeNotifier(calls),
        status=FakeStatus(),
        lock=FakeLock(calls),
        stamp=FakeStamp(calls),
        clock=lambda: T0,
    )


def _chain_events(ports: Ports) -> list[AuditEvent]:
    return [e for e in ports.audit.events if e.rule_id == "chain"]


def test_orchestrator_records_shadow_would_upgrade_and_disagreement():
    outcome = TriageOutcome(
        "transient",
        "shadow",
        "claude",
        local_flagged=True,
        disagreement=True,
        would_be="upgrade",
    )
    ports = _ports(outcome)
    run_once(ports, Config())
    (event,) = _chain_events(ports)
    assert event.booleans == {
        "would_upgrade": True,
        "would_hold": False,
        "disagreement": True,
        "local_flagged": True,
    }
    assert shadow_report(ports.audit.events) == ShadowReport(1, 1, 0, 1)


def test_orchestrator_records_shadow_hold_and_outage_distinctly():
    held = _ports(TriageOutcome("hold", "claude hold", "claude", would_be="hold"))
    run_once(held, Config())
    down = _ports(TriageOutcome("transient", "claude unavailable", "claude"))
    run_once(down, Config())
    assert shadow_report(held.audit.events) == ShadowReport(1, 0, 1, 0)
    assert shadow_report(down.audit.events) == ShadowReport(1, 0, 0, 0)
