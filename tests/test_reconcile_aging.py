"""T12: reconcile user pins, hold aging, stale escalation, and R14 hold reuse."""

from collections.abc import Iterable, Mapping
from dataclasses import replace
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
from morning_brew.aging import (
    Aging,
    digest_due,
    hold_aging,
    render_digest,
    stale_holds,
)
from morning_brew.config import Config
from morning_brew.domain import Decision, Hold, HoldKind, TriageOutcome, Verdict
from morning_brew.orchestrator import run_once
from morning_brew.ports import Ports
from morning_brew.reconcile import reconcile

NOW = datetime(2026, 11, 17, 8, 0, tzinfo=UTC)


def hold(
    name: str,
    *,
    kind: HoldKind = HoldKind.DECIDED,
    held_at_version: str = "1.0.1",
    age: int = 0,
    blocked_by: str | None = None,
    skipped_for_version: str | None = None,
    reason: str = "held earlier",
) -> Hold:
    return Hold(
        name=name,
        kind=kind,
        held_at_version=held_at_version,
        reason=reason,
        first_held=NOW - timedelta(days=age),
        blocked_by=blocked_by,
        skipped_for_version=skipped_for_version,
    )


def decided(name: str = "d", **kw) -> Hold:  # noqa: ANN003
    return hold(name, kind=HoldKind.DECIDED, **kw)


def transient(name: str = "t", **kw) -> Hold:  # noqa: ANN003
    return hold(name, kind=HoldKind.TRANSIENT, **kw)


def store_with(*holds: Hold) -> FakeHolds:
    store = FakeHolds()
    for h in holds:
        store.put(h)
    return store


def ports_with(
    *,
    existing_holds: Iterable[Hold] = (),
    current: Mapping[str, str],
    rules_say_eligible: Iterable[str] = (),
    grey: Iterable[str] = (),
    groups: list[list[str]] | None = None,
    triage_outcomes: Mapping[str, TriageOutcome] | None = None,
) -> tuple[Ports, list[str]]:
    calls: list[str] = []
    snap = make_snapshot(*(make_candidate(n, current=v) for n, v in current.items()))
    # Every candidate not named GREY gets an ELIGIBLE rule decision;
    # ``rules_say_eligible`` names that intent explicitly at call sites.
    grey = set(grey)
    assert grey.isdisjoint(rules_say_eligible)
    decisions = [
        Decision(n, Verdict.GREY, "R11", "caveat changed")
        if n in grey
        else Decision(n, Verdict.ELIGIBLE, "R12", "clean")
        for n in current
    ]
    outcomes = triage_outcomes or {
        n: TriageOutcome("hold", "migration", "claude") for n in grey
    }
    ports = Ports(
        brew=FakeBrew(calls, [snap]),
        live=FakeLive(),
        rules=recording_rules(calls, decisions),
        components=grouping_components(calls, groups or [[n] for n in current]),
        reconcile=recording_reconcile(calls),
        triage=FakeTriage(calls, outcomes),
        holds=store_with(*existing_holds),
        seen=FakeSeen(),
        audit=FakeAudit(calls),
        notifier=FakeNotifier(calls),
        status=FakeStatus(),
        lock=FakeLock(calls),
        stamp=FakeStamp(calls),
        clock=lambda: NOW,
    )
    return ports, calls


# --- pure helpers -----------------------------------------------------------


def test_newly_pinned_package_releases_hold_without_pin_calls() -> None:
    snap = make_snapshot(make_candidate("x", pinned=True), make_candidate("y"))
    store = store_with(decided("x"), decided("y"))
    assert reconcile(snap, store) == ["x"]
    assert store.get("x") is None
    assert store.get("y") is not None  # unpinned holds are untouched


def test_reconcile_without_holds_releases_nothing() -> None:
    snap = make_snapshot(make_candidate("x", pinned=True))
    assert reconcile(snap, FakeHolds()) == []


def test_aging_counts_decided_only_for_age() -> None:
    a = hold_aging(
        [
            decided("d1", age=3),
            decided("d2", age=20, blocked_by="awscli"),
            transient("t1", age=40),
        ],
        NOW,
    )
    assert (
        a.decided_count,
        a.oldest_decided_days,
        a.blocked_by_dep_count,
        a.transient_count,
    ) == (2, 20, 1, 1)
    assert hold_aging([], NOW) == Aging(0, None, 0, 0)


def test_stale_threshold_and_digest() -> None:
    holds = [
        decided("old", age=20),
        decided("new", age=3),
        decided("edge", age=14),  # stale only *after* 14 days
        transient("flaky", age=40),  # transient never ages
    ]
    assert [h.name for h in stale_holds(holds, NOW, 14)] == ["old"]
    assert digest_due(None, NOW)
    assert not digest_due(NOW - timedelta(days=6), NOW)
    assert digest_due(NOW - timedelta(days=7), NOW)


def test_render_digest_lists_counts_and_stale_names() -> None:
    holds = [decided("old", age=20, reason="data migration"), decided("new", age=3)]
    text = render_digest(hold_aging(holds, NOW), stale_holds(holds, NOW, 14))
    assert "2 decided" in text and "oldest 20 days" in text
    assert "old" in text and "data migration" in text
    assert "new:" not in text


# --- R14 wiring in run_once -------------------------------------------------


def test_owner_skip_is_never_auto_upgraded() -> None:  # R14
    ports, calls = ports_with(
        existing_holds=[
            # kind TRANSIENT so the DECIDED assertion below is not vacuous
            hold(
                "a",
                kind=HoldKind.TRANSIENT,
                held_at_version="1.0",
                skipped_for_version="1.0",
            ),
        ],
        current={"a": "1.0"},
        rules_say_eligible={"a"},
    )
    r = run_once(ports, Config())
    assert "brew.upgrade:a" not in calls
    assert ports.holds.get("a").kind is HoldKind.DECIDED
    assert ports.holds.get("a").skipped_for_version == "1.0"
    assert r.summary.upgraded == ()
    events = ports.audit.events_for("a")
    assert events[-1].rule_id == "skip" and events[-1].verdict == "hold"


def test_owner_skip_blocks_its_component() -> None:  # R14
    ports, calls = ports_with(
        existing_holds=[decided("a", held_at_version="1.0", skipped_for_version="1.0")],
        current={"a": "1.0", "b": "2.0"},
        rules_say_eligible={"a", "b"},
        groups=[["a", "b"]],
    )
    run_once(ports, Config())
    assert not any(c.startswith("brew.upgrade") for c in calls)


def test_owner_skip_lapses_on_new_version() -> None:  # FR-061
    ports, calls = ports_with(
        existing_holds=[decided("a", held_at_version="1.0", skipped_for_version="1.0")],
        current={"a": "1.1"},
        rules_say_eligible={"a"},
    )
    run_once(ports, Config())
    assert "brew.upgrade:a" in calls


def test_decided_hold_same_version_is_not_retriaged() -> None:  # R14
    ports, calls = ports_with(
        existing_holds=[decided("g", held_at_version="0.1", reason="data migration")],
        current={"g": "0.1"},
        grey={"g"},
    )
    r = run_once(ports, Config())
    assert len(ports.triage.requests) == 0
    assert not any(c.startswith("triage") for c in calls)
    assert "brew.upgrade:g" not in calls
    kept = ports.holds.get("g")
    assert kept.kind is HoldKind.DECIDED and kept.reason == "data migration"
    assert r.summary.decided_holds == 1


def test_decided_hold_same_version_dry_run_makes_no_model_call() -> None:
    ports, _ = ports_with(
        existing_holds=[decided("g", held_at_version="0.1")],
        current={"g": "0.1"},
        grey={"g"},
    )
    run_once(ports, Config(), dry_run=True)
    assert len(ports.triage.requests) == 0


def test_decided_hold_new_version_is_retriaged() -> None:  # R14
    ports, _ = ports_with(
        existing_holds=[decided("g", held_at_version="0.1")],
        current={"g": "0.2"},
        grey={"g"},
    )
    run_once(ports, Config())
    assert len(ports.triage.requests) == 1
    assert ports.holds.get("g").held_at_version == "0.2"


def test_transient_hold_is_retried_every_run() -> None:  # R14
    ports, calls = ports_with(
        existing_holds=[transient("g", held_at_version="0.1")],
        current={"g": "0.1"},
        grey={"g"},
        triage_outcomes={"g": TriageOutcome("upgrade", "ok", "claude")},
    )
    r = run_once(ports, Config())
    assert len(ports.triage.requests) == 1
    assert "brew.upgrade:g" in calls
    # an upgraded package is no longer outdated: its hold row goes
    assert ports.holds.get("g") is None
    assert r.summary.transient == 0 and r.summary.quality == "full"


def test_hold_released_when_no_longer_outdated() -> None:  # R14
    ports, _ = ports_with(
        existing_holds=[decided("gone"), decided("kept", held_at_version="1.0")],
        current={"kept": "1.0"},
        grey={"kept"},
    )
    run_once(ports, Config())
    assert ports.holds.get("gone") is None
    assert ports.holds.get("kept") is not None


def test_dry_run_releases_nothing() -> None:
    ports, _ = ports_with(existing_holds=[decided("gone")], current={"a": "1.0"})
    run_once(ports, Config(), dry_run=True)
    assert ports.holds.get("gone") is not None


def test_reconcile_releases_are_audited() -> None:
    ports, calls = ports_with(
        existing_holds=[decided("p")], current={"a": "1.0", "p": "1.0"}
    )
    snap = make_snapshot(
        make_candidate("a", current="1.0"),
        make_candidate("p", current="1.0", pinned=True),
    )
    decisions = [
        Decision("a", Verdict.ELIGIBLE, "R12", "clean"),
        Decision("p", Verdict.SKIP, "R02", "R02 pinned p"),
    ]
    ports = replace(
        ports,
        brew=FakeBrew(calls, [snap]),
        rules=recording_rules(calls, decisions),
        reconcile=reconcile,
        audit=FakeAudit(calls),
        notifier=FakeNotifier(calls),
        stamp=FakeStamp(calls),
        lock=FakeLock(calls),
    )
    run_once(ports, Config())
    released = [e for e in ports.audit.events if e.stage == "reconcile"]
    assert [(e.package, e.action_result) for e in released] == [("p", "released")]
    assert ports.holds.get("p") is None
    # audit stays after the upgrades in the pipeline order
    assert calls.index("audit") > calls.index("brew.upgrade:a")


def test_summary_reports_aging_and_stale_escalates() -> None:
    ports, _ = ports_with(
        existing_holds=[
            decided("old", held_at_version="1.0", age=30, blocked_by="awscli"),
            decided("new", held_at_version="1.0", age=2),
        ],
        current={"old": "1.0", "new": "1.0", "a": "1.0"},
        grey={"old", "new"},
    )
    r = run_once(ports, Config())
    s = r.summary
    assert (s.decided_holds, s.oldest_decided_hold_days, s.transient) == (2, 30, 0)
    assert any("stale" in line and "old" in line for line in s.anomalies)
    assert not any("new" in line for line in s.anomalies)
    assert ports.notifier.sent[-1][1] is True  # urgent


def test_stale_threshold_comes_from_config() -> None:
    ports, _ = ports_with(
        existing_holds=[decided("old", held_at_version="1.0", age=30)],
        current={"old": "1.0"},
        grey={"old"},
    )
    r = run_once(ports, Config(stale_hold_days=60))
    assert r.summary.anomalies == ()
    assert ports.notifier.sent[-1][1] is False
