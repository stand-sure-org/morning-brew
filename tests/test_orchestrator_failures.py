from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

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
from morning_brew.domain import (
    BrewUpdateError,
    Decision,
    Hold,
    HoldKind,
    StateUnwritable,
    Summary,
    TriageOutcome,
    TriageRequest,
    UpgradeResult,
    Verdict,
)
from morning_brew.orchestrator import run_once
from morning_brew.ports import Ports

NOW = datetime(2026, 11, 17, 8, 0, tzinfo=UTC)


def _elig(name: str) -> Decision:
    return Decision(name, Verdict.ELIGIBLE, "R12", "clean")


class RaisingNotifier:
    def notify(self, summary: Summary, *, urgent: bool) -> None:
        raise RuntimeError("osascript gone")


class UnwritableHolds(FakeHolds):
    def ensure_writable(self) -> None:
        raise StateUnwritable("read-only state dir")


class ExplodingTriage:
    def triage(self, request: TriageRequest) -> TriageOutcome:
        raise AssertionError("no model call expected")


@dataclass
class Clock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now


class AdvancingTriage(FakeTriage):
    clock: Clock | None = None

    def triage(self, request: TriageRequest) -> TriageOutcome:
        out = super().triage(request)
        if self.clock is not None:
            self.clock.now += timedelta(minutes=21)
        return out


@pytest.fixture
def calls() -> list[str]:
    return []


@pytest.fixture
def ports_with(calls: list[str]):  # noqa: ANN201
    def build(
        *,
        names: tuple[str, ...] = ("a", "b", "c"),
        groups: list[list[str]] | None = None,
        decisions: list[Decision] | None = None,
        snapshots: list[Any] | None = None,
        brew: FakeBrew | None = None,
        **overrides: Any,
    ) -> Ports:
        snap = make_snapshot(*(make_candidate(n) for n in names))
        ports = Ports(
            brew=brew or FakeBrew(calls, snapshots or [snap]),
            live=FakeLive(),
            rules=recording_rules(calls, decisions or [_elig(n) for n in names]),
            components=grouping_components(calls, groups or [[n] for n in names]),
            reconcile=recording_reconcile(calls),
            triage=FakeTriage(calls, {}),
            holds=FakeHolds(),
            seen=FakeSeen(),
            audit=FakeAudit(calls),
            notifier=FakeNotifier(calls),
            status=FakeStatus(),
            lock=FakeLock(calls),
            stamp=FakeStamp(calls),
            clock=lambda: NOW,
        )
        return replace(ports, **overrides)

    return build


def test_update_failure_aborts_before_mutation(ports_with, calls) -> None:
    brew = FakeBrew(calls, [make_snapshot()], raise_on_update=BrewUpdateError("net"))
    r = run_once(ports_with(brew=brew), Config())
    assert r.exit_code == 75 and r.summary is None
    assert not any(
        c.startswith(("brew.upgrade", "brew.cleanup", "stamp.write")) for c in calls
    )


def test_nothing_outdated_is_a_clean_noop(ports_with, calls) -> None:  # Review Focus 1
    ports = ports_with(
        names=(), decisions=[], triage=ExplodingTriage(), snapshots=[make_snapshot()]
    )
    r = run_once(ports, Config())
    assert r.exit_code == 0 and r.summary.quality == "full"
    assert "stamp.write" in calls
    assert not any(c.startswith("brew.upgrade") for c in calls)


def test_partial_failure_continues_and_retries_later(ports_with, calls) -> None:
    brew = FakeBrew(
        calls,
        [make_snapshot(*(make_candidate(n) for n in "abc"))],
        upgrade_results={"b": UpgradeResult(False, "boom")},
    )
    ports = ports_with(brew=brew)
    r = run_once(ports, Config())
    assert r.summary.upgraded == ("a", "c") and r.summary.failed == ("b",)
    assert ports.holds.list_all() == []  # failure is not a hold
    assert "brew.upgrade:c" in calls


def test_brew_busy_is_transient_not_failed(ports_with, calls) -> None:  # Review Focus 2
    brew = FakeBrew(
        calls,
        [make_snapshot(*(make_candidate(n) for n in "abc"))],
        busy_on=frozenset({"b"}),
    )
    ports = ports_with(brew=brew)
    r = run_once(ports, Config())
    assert r.summary.failed == () and r.summary.transient == 1
    assert r.summary.upgraded == ("a", "c")
    hold = ports.holds.get("b")
    assert hold is not None and hold.kind is HoldKind.TRANSIENT
    assert r.summary.quality == "degraded"
    assert ports.stamp.written[-1].quality == "degraded"


def test_closure_mismatch_excludes_component_and_audits_anomaly(
    ports_with, calls
) -> None:
    brew = FakeBrew(
        calls,
        [make_snapshot(*(make_candidate(n) for n in "abc"))],
        closure={"a": frozenset({"a", "zzz"})},
    )
    r = run_once(ports_with(brew=brew), Config())
    assert "brew.upgrade:a" not in calls
    assert any("closure" in x for x in r.summary.anomalies)
    assert r.summary.upgraded == ("b", "c")


def test_verify_anomaly_is_urgent(ports_with, calls) -> None:
    pre = make_snapshot(make_candidate("a"), make_candidate("held_pkg"))
    post = make_snapshot(make_candidate("a"))  # held_pkg changed under us
    decisions = [
        _elig("a"),
        Decision("held_pkg", Verdict.HOLD, "R3", "held", HoldKind.DECIDED),
    ]
    ports = ports_with(
        names=("a", "held_pkg"), decisions=decisions, snapshots=[pre, post]
    )
    r = run_once(ports, Config())
    assert ports.notifier.sent[-1][1] is True
    assert "held_pkg" in r.summary.anomalies[0]


def test_budget_exceeded_makes_remaining_grey_transient(ports_with, calls) -> None:
    clock = Clock()
    triage = AdvancingTriage(
        calls,
        {
            "g1": TriageOutcome("upgrade", "ok", "claude"),
            "g2": TriageOutcome("upgrade", "ok", "claude"),
        },
    )
    triage.clock = clock
    grey = [Decision(n, Verdict.GREY, "R11", "x") for n in ("g1", "g2")]
    ports = ports_with(names=("g1", "g2"), decisions=grey, triage=triage, clock=clock)
    r = run_once(ports, Config())
    assert "triage:g1" in calls and "triage:g2" not in calls
    assert r.summary.transient == 1
    hold = ports.holds.get("g2")
    assert hold is not None and "budget" in hold.reason
    assert r.summary.upgraded == ("g1",)


def test_self_dependency_component_defers_with_copyable_command(
    ports_with, calls
) -> None:
    defer = [Decision(n, Verdict.DEFER, "R13", "self dep") for n in ("fnox", "usage")]
    ports = ports_with(
        names=("fnox", "usage"),
        decisions=defer,
        groups=[["fnox", "usage"]],
        self_deps=frozenset({"fnox"}),
    )
    r = run_once(ports, Config())
    assert "brew.upgrade:fnox,usage" not in calls
    assert r.summary.self_dep_commands == ("brew upgrade -- fnox usage",)


def test_notifier_failure_does_not_fail_run(ports_with, calls) -> None:
    r = run_once(ports_with(notifier=RaisingNotifier()), Config())
    assert r.exit_code == 0
    assert "stamp.write" in calls


def test_unwritable_state_exits_1_before_update(ports_with, calls) -> None:
    r = run_once(ports_with(holds=UnwritableHolds()), Config())
    assert r.exit_code == 1
    assert "brew.update" not in calls and "stamp.write" not in calls


def test_rules_only_run_makes_no_model_call(ports_with) -> None:  # FR-093
    r = run_once(ports_with(triage=ExplodingTriage()), Config())
    assert r.exit_code == 0 and r.summary.upgraded == ("a", "b", "c")


def test_existing_hold_keeps_first_held_and_skip(ports_with) -> None:
    earlier = NOW - timedelta(days=5)
    holds = FakeHolds()
    holds.put(
        Hold(
            "h",
            HoldKind.DECIDED,
            "0.9",
            "old",
            earlier,
            blocked_by="x",
            model_reasoning="old reasoning",
            skipped_for_version="1.0.0",
        )
    )
    decisions = [Decision("h", Verdict.HOLD, "R3", "new reason", HoldKind.DECIDED)]
    run_once(ports_with(names=("h",), decisions=decisions, holds=holds), Config())
    hold = holds.get("h")
    assert hold.first_held == earlier and hold.skipped_for_version == "1.0.0"
    assert hold.reason == "new reason" and hold.held_at_version == "1.0.1"
    assert hold.model_reasoning == "old reasoning"
    assert hold.blocked_by is None


def test_triage_reasoning_is_stored_on_hold(ports_with, calls) -> None:
    triage = FakeTriage(
        calls, {"g": TriageOutcome("hold", "migration", "claude", reasoning="because")}
    )
    grey = [Decision("g", Verdict.GREY, "R11", "x")]
    ports = ports_with(names=("g",), decisions=grey, triage=triage)
    run_once(ports, Config())
    hold = ports.holds.get("g")
    assert hold is not None and hold.model_reasoning == "because"


def test_notifier_failure_and_unwritable_state(ports_with, calls) -> None:
    assert run_once(ports_with(notifier=RaisingNotifier()), Config()).exit_code == 0
    calls.clear()
    assert run_once(ports_with(holds=UnwritableHolds()), Config()).exit_code == 1
    assert "brew.update" not in calls
