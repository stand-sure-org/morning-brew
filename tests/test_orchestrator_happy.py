from dataclasses import replace
from datetime import UTC, datetime

import pytest

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
from morning_brew.domain import Decision, HoldKind, TriageOutcome, Verdict
from morning_brew.orchestrator import run_once
from morning_brew.ports import Ports
from morning_brew.rules import apply_outcome

NOW = datetime(2026, 11, 17, 8, 0, tzinfo=UTC)


@pytest.fixture
def calls() -> list[str]:
    return []


@pytest.fixture
def ports(calls: list[str]) -> Ports:
    snap = make_snapshot(
        make_candidate("a"),
        make_candidate("b", runtime_deps=("c",), is_leaf=True),
        make_candidate("c", is_leaf=False, dependents=1),
        make_candidate("grype", caveats="restart after upgrade"),
    )
    decisions = [
        Decision("a", Verdict.ELIGIBLE, "R12", "clean"),
        Decision("b", Verdict.ELIGIBLE, "R12", "clean"),
        Decision("c", Verdict.ELIGIBLE, "R12", "clean"),
        Decision("grype", Verdict.GREY, "R11", "caveat changed"),
    ]
    return Ports(
        brew=FakeBrew(calls, [snap]),
        live=FakeLive(),
        rules=recording_rules(calls, decisions),
        components=grouping_components(calls, [["a"], ["b", "c"], ["grype"]]),
        reconcile=recording_reconcile(calls),
        triage=FakeTriage(calls, {"grype": TriageOutcome("upgrade", "ok", "claude")}),
        holds=FakeHolds(),
        seen=FakeSeen(),
        audit=FakeAudit(calls),
        notifier=FakeNotifier(calls),
        status=FakeStatus(),
        lock=FakeLock(calls),
        stamp=FakeStamp(calls),
        clock=lambda: NOW,
    )


def test_call_order_and_explicit_upgrades(ports: Ports, calls: list[str]) -> None:
    result = run_once(ports, Config())
    assert calls == [
        "lock",
        "brew.update",
        "brew.snapshot",
        "reconcile",
        "brew.cleanup",
        "rules",
        "components",
        "triage:grype",
        "components",
        "brew.upgrade:a",
        "brew.upgrade:b,c",
        "brew.upgrade:grype",
        "brew.snapshot",
        "audit",
        "notify",
        "stamp.write",
    ]
    assert calls.index("triage:grype") < min(
        i for i, c in enumerate(calls) if c.startswith("brew.upgrade")
    )
    assert result.exit_code == 0 and result.summary.upgraded == (
        "a",
        "b",
        "c",
        "grype",
    )  # grype resolved GREY -> ELIGIBLE singleton


def test_happy_path_records_state(ports: Ports) -> None:
    run_once(ports, Config())
    assert ports.seen.first[("grype", "1.0.1")] == NOW  # first-seen before rules
    assert ports.seen.cleanup_queries == [(0, Config().cleanup_runs)]
    assert ports.seen.upgrades == {1: ["a", "b", "c", "grype"]}
    stamp = ports.stamp.written[-1]
    assert stamp.quality == "full" and stamp.full_runs == 1
    assert stamp.last_full_success == NOW
    events = {e.package: e for e in ports.audit.events}
    assert set(events) == {"a", "b", "c", "grype"}
    assert events["b"].component == "b,c" and events["a"].component is None
    assert events["grype"].verdict == "eligible" and events["grype"].rule_id == "chain"


def test_hold_decisions_are_stored(ports: Ports, calls: list[str]) -> None:
    ports.triage.outcomes = {"grype": TriageOutcome("hold", "data migration", "claude")}
    result = run_once(ports, Config())
    hold = ports.holds.get("grype")
    assert hold is not None and hold.kind is HoldKind.DECIDED
    assert hold.held_at_version == "1.0.1" and hold.first_held == NOW
    assert "brew.upgrade:grype" not in calls
    assert result.summary.decided_holds == 1


def test_apply_outcome_only_moves_grey() -> None:
    grey = Decision("g", Verdict.GREY, "R11", "x")
    assert (
        apply_outcome(grey, TriageOutcome("upgrade", "ok", "claude")).verdict
        is Verdict.ELIGIBLE
    )
    assert (
        apply_outcome(grey, TriageOutcome("hold", "no", "claude")).hold_kind
        is HoldKind.DECIDED
    )
    assert (
        apply_outcome(grey, TriageOutcome("transient", "shadow", "chain")).hold_kind
        is HoldKind.TRANSIENT
    )
    eligible = Decision("e", Verdict.ELIGIBLE, "R12", "clean")
    assert apply_outcome(eligible, TriageOutcome("hold", "no", "claude")) is eligible


def test_self_deps_reach_rule_context(ports: Ports) -> None:  # [ruling R13]
    ports = replace(ports, self_deps=frozenset({"fnox"}))
    run_once(ports, Config())
    assert ports.rules.contexts[-1].self_deps == frozenset({"fnox"})
