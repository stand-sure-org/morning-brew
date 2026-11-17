import os
import stat
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
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
from morning_brew.domain import Decision, LockHeld, Stamp, Verdict
from morning_brew.orchestrator import run_once
from morning_brew.ports import Ports
from morning_brew.schedule import dead_man, should_run
from morning_brew.store.atomic import read_json_or_none, write_json_atomic
from morning_brew.store.lock import FileLock
from morning_brew.store.stamp import FileStampStore


def at(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=UTC)


def full(t: datetime) -> Stamp:
    return Stamp(last_full_success=t, last_attempt=t, quality="full", full_runs=3)


def degraded(t: datetime) -> Stamp:
    return Stamp(
        last_full_success=None, last_attempt=t, quality="degraded", full_runs=3
    )


@pytest.mark.parametrize(
    "now,stamp,expected",
    [
        (at("2026-11-17 06:59"), None, False),  # before earliest_hour 7
        (at("2026-11-17 08:00"), None, True),  # no stamp, after earliest hour
        (at("2026-11-17 08:00"), full(at("2026-11-17 07:10")), False),  # ran today
        (at("2026-11-17 08:00"), degraded(at("2026-11-17 07:10")), True),  # retries
        (at("2026-11-18 00:10"), full(at("2026-11-17 23:50")), False),  # too early
        (at("2026-11-18 07:30"), full(at("2026-11-17 23:50")), True),  # new day
        (at("2026-11-17 08:00"), full(at("2026-11-17 09:00")), True),  # future stamp
    ],
)
def test_gate(now, stamp, expected) -> None:
    assert should_run(now, stamp, 7).run is expected


def test_dead_man() -> None:
    assert dead_man(at("2026-11-19 08:00"), full(at("2026-11-17 07:00"))) is True
    assert dead_man(at("2026-11-19 06:00"), full(at("2026-11-17 07:00"))) is False
    assert dead_man(at("2026-11-19 08:00"), None) is False


def test_second_lock_raises(tmp_path: Path) -> None:
    with FileLock(tmp_path / "l").acquire():
        with pytest.raises(LockHeld):
            FileLock(tmp_path / "l").acquire().__enter__()
    with FileLock(tmp_path / "l").acquire():  # released afterwards
        pass


def test_corrupt_stamp_reads_as_none(tmp_path: Path) -> None:  # Review Focus 3
    (tmp_path / "stamp.json").write_text("{trunc")
    assert FileStampStore(tmp_path / "stamp.json").read() is None
    assert read_json_or_none(tmp_path / "missing.json") is None


def test_stamp_roundtrip(tmp_path: Path) -> None:
    store = FileStampStore(tmp_path / "state" / "stamp.json")
    stamp = full(at("2026-11-17 07:10"))
    store.write(stamp)
    assert store.read() == stamp
    assert stat.S_IMODE(os.stat(tmp_path / "state").st_mode) == 0o700


def test_atomic_write_keeps_old_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "s.json"
    write_json_atomic(target, {"v": 1})

    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        write_json_atomic(target, {"v": 2})
    assert read_json_or_none(target) == {"v": 1}
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]  # no temp litter


NOW = at("2026-11-17 08:00")


class Clock:
    def __call__(self) -> datetime:
        return NOW


class LockedLock:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    def acquire(self) -> Any:
        self.calls.append("lock")
        raise LockHeld("held")


@pytest.fixture
def calls() -> list[str]:
    return []


@pytest.fixture
def ports_with(calls: list[str]):  # noqa: ANN201
    def build(**overrides: Any) -> Ports:
        names = ("a", "b")
        snap = make_snapshot(*(make_candidate(n) for n in names))
        decisions = [Decision(n, Verdict.ELIGIBLE, "R12", "clean") for n in names]
        ports = Ports(
            brew=FakeBrew(calls, [snap]),
            live=FakeLive(),
            rules=recording_rules(calls, decisions),
            components=grouping_components(calls, [["a"], ["b"]]),
            reconcile=recording_reconcile(calls),
            triage=FakeTriage(calls, {}),
            holds=FakeHolds(),
            seen=FakeSeen(),
            audit=FakeAudit(calls),
            notifier=FakeNotifier(calls),
            status=FakeStatus(),
            lock=FakeLock(calls),
            stamp=FakeStamp(calls),
            clock=Clock(),
        )
        return replace(ports, **overrides)

    return build


def test_dry_run_makes_no_mutating_call(ports_with, calls) -> None:
    ports = ports_with()
    r = run_once(ports, Config(), dry_run=True)
    assert r.exit_code == 0
    assert not any(
        c.startswith(("brew.update", "brew.upgrade", "brew.cleanup", "stamp.write"))
        for c in calls
    )
    assert ports.seen.first == {}
    assert ports.holds.list_all() == []
    assert ports.status.last is None
    assert ports.notifier.sent == []


def test_dry_run_bypasses_gate_and_audits_each_decision(ports_with, calls) -> None:
    done = full(at("2026-11-17 07:10"))  # real run would be gated
    stamp = FakeStamp(calls, stamp=done)
    ports = ports_with(stamp=stamp)
    assert run_once(ports, Config()).summary is None  # gate closes a real run
    r = run_once(ports, Config(), dry_run=True)
    assert r.summary is not None
    events = ports.audit.events
    assert sorted(e.package for e in events) == ["a", "b"]
    assert {e.stage for e in events} == {"dry_run"}
    assert all(e.verdict == "eligible" and e.action_result is None for e in events)
    assert stamp.written == []


def test_gated_noop_writes_nothing(ports_with, calls) -> None:
    stamp = FakeStamp(calls, stamp=full(at("2026-11-17 07:10")))
    ports = ports_with(stamp=stamp)
    r = run_once(ports, Config())
    assert r.exit_code == 0 and r.summary is None
    assert calls == ["lock"]
    assert ports.notifier.sent == []


def test_locked_run_exits_75_without_brew(ports_with, calls) -> None:
    for dry in (False, True):
        calls.clear()
        r = run_once(ports_with(lock=LockedLock(calls)), Config(), dry_run=dry)
        assert r.exit_code == 75 and r.summary is None
        assert calls == ["lock"]


def test_dead_man_surfaces_on_gated_noop(ports_with, calls) -> None:
    old = full(at("2026-11-14 07:00"))
    # now is 06:00 on the 17th: before earliest hour, so gated; 71 h since success
    ports = ports_with(
        stamp=FakeStamp(calls, stamp=old), clock=lambda: at("2026-11-17 06:00")
    )
    r = run_once(ports, Config())
    assert r.exit_code == 0
    assert not any(c.startswith("brew.") for c in calls)
    assert len(ports.notifier.sent) == 1
    summary, urgent = ports.notifier.sent[0]
    assert urgent is True and summary.dead_man is True


def test_full_run_stamps_success_degraded_does_not(ports_with, calls) -> None:
    ports = ports_with()
    run_once(ports, Config())
    s = ports.stamp.written[-1]
    assert s.quality == "full" and s.full_runs == 1
    assert s.last_full_success == NOW and s.last_attempt == NOW


def never_succeeded(first: datetime) -> Stamp:
    return Stamp(
        last_full_success=None,
        last_attempt=first,
        quality="degraded",
        full_runs=0,
        first_attempt=first,
    )


def test_dead_man_trips_when_never_succeeded() -> None:
    now = at("2026-11-19 08:00")
    assert dead_man(now, never_succeeded(at("2026-11-17 07:00"))) is True  # 49 h


def test_dead_man_quiet_on_fresh_install() -> None:
    now = at("2026-11-19 08:00")
    assert dead_man(now, never_succeeded(at("2026-11-19 06:00"))) is False  # 2 h
    assert dead_man(now, None) is False


def test_stamp_without_first_attempt_key_reads(tmp_path: Path) -> None:
    path = tmp_path / "stamp.json"
    path.write_text(
        '{"last_full_success": null, "last_attempt": "2026-11-17T07:10:00+00:00",'
        ' "quality": "degraded", "full_runs": 2}'
    )
    stamp = FileStampStore(path).read()
    assert stamp is not None and stamp.first_attempt is None and stamp.full_runs == 2


def test_stamp_write_keeps_first_attempt_once(ports_with, calls) -> None:
    first = at("2026-11-10 07:00")
    prev = Stamp(None, first, "degraded", 0, first_attempt=first)
    ports = ports_with(stamp=FakeStamp(calls, stamp=prev))
    run_once(ports, Config())
    assert ports.stamp.written[-1].first_attempt == first
    fresh = ports_with()
    run_once(fresh, Config())
    assert fresh.stamp.written[-1].first_attempt == NOW
