"""Recording fakes for the orchestrator's ports.

Every fake that matters for call order appends a tag to one shared ``calls``
list. Ports whose calls are bookkeeping (live set, first-seen, holds, status)
stay silent so the call-order assertions read as the run pipeline.
"""

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime

from morning_brew.domain import (
    AuditEvent,
    ComponentPlan,
    Decision,
    Hold,
    Snapshot,
    Stamp,
    Summary,
    TriageOutcome,
    TriageRequest,
    UpgradeResult,
    Verdict,
)
from morning_brew.ports import HoldStorePort


@dataclass
class FakeBrew:
    calls: list[str]
    snapshots: list[Snapshot]
    upgrades: list[tuple[str, ...]] = field(default_factory=list)
    cleaned: list[tuple[str, ...]] = field(default_factory=list)

    def update(self) -> None:
        self.calls.append("brew.update")

    def snapshot(self) -> Snapshot:
        self.calls.append("brew.snapshot")
        if len(self.snapshots) > 1:
            return self.snapshots.pop(0)
        return self.snapshots[0]

    def upgrade(self, members: Sequence[str]) -> UpgradeResult:
        self.calls.append("brew.upgrade:" + ",".join(members))
        self.upgrades.append(tuple(members))
        return UpgradeResult(ok=True, stderr_tail="")

    def dry_run_closure(self, members: Sequence[str]) -> frozenset[str]:
        return frozenset(members)

    def cleanup(self, packages: Sequence[str]) -> None:
        self.calls.append("brew.cleanup")
        self.cleaned.append(tuple(packages))


@dataclass
class FakeLive:
    live: frozenset[str] = frozenset()

    def live_set(self, snapshot: Snapshot) -> frozenset[str]:
        return self.live


@dataclass
class FakeTriage:
    calls: list[str]
    outcomes: Mapping[str, TriageOutcome]
    requests: list[TriageRequest] = field(default_factory=list)

    def triage(self, request: TriageRequest) -> TriageOutcome:
        self.calls.append("triage:" + request.full_name)
        self.requests.append(request)
        return self.outcomes[request.full_name]


@dataclass
class FakeHolds:
    holds: dict[str, Hold] = field(default_factory=dict)

    def ensure_writable(self) -> None:
        return None

    def get(self, name: str) -> Hold | None:
        return self.holds.get(name)

    def put(self, hold: Hold) -> None:
        self.holds[hold.name] = hold

    def release(self, name: str) -> None:
        self.holds.pop(name, None)

    def list_all(self) -> list[Hold]:
        return list(self.holds.values())

    def mark_skipped(self, name: str, version: str) -> None:
        return None


@dataclass
class FakeSeen:
    first: dict[tuple[str, str], datetime] = field(default_factory=dict)
    hashes: dict[str, str] = field(default_factory=dict)
    upgrades: dict[int, list[str]] = field(default_factory=dict)
    due: list[str] = field(default_factory=list)
    cleanup_queries: list[tuple[int, int]] = field(default_factory=list)

    def record_first_seen(self, name: str, version: str, now: datetime) -> None:
        self.first.setdefault((name, version), now)

    def first_seen(self, name: str, version: str) -> datetime | None:
        return self.first.get((name, version))

    def caveat_hash(self, name: str) -> str | None:
        return self.hashes.get(name)

    def record_caveat_hash(self, name: str, h: str) -> None:
        self.hashes[name] = h

    def record_upgrade(self, run_seq: int, names: Sequence[str]) -> None:
        self.upgrades.setdefault(run_seq, []).extend(names)

    def due_for_cleanup(self, current_seq: int, runs: int) -> list[str]:
        self.cleanup_queries.append((current_seq, runs))
        return list(self.due)


@dataclass
class FakeAudit:
    """Appends one ``"audit"`` tag per run (on the first event), not per event."""

    calls: list[str]
    events: list[AuditEvent] = field(default_factory=list)
    _tagged_runs: set[str] = field(default_factory=set)

    def record(self, event: AuditEvent) -> None:
        if event.run_id not in self._tagged_runs:
            self._tagged_runs.add(event.run_id)
            self.calls.append("audit")
        self.events.append(event)

    def events_for(self, package: str) -> list[AuditEvent]:
        return [e for e in self.events if e.package == package]


@dataclass
class FakeNotifier:
    calls: list[str]
    sent: list[tuple[Summary, bool]] = field(default_factory=list)

    def notify(self, summary: Summary, *, urgent: bool) -> None:
        self.calls.append("notify")
        self.sent.append((summary, urgent))


@dataclass
class FakeStatus:
    last: Summary | None = None

    def write(self, summary: Summary) -> None:
        self.last = summary

    def read(self) -> Summary | None:
        return self.last


@dataclass
class FakeLock:
    calls: list[str]

    @contextmanager
    def _held(self) -> Iterator[None]:
        yield

    def acquire(self) -> AbstractContextManager[None]:
        self.calls.append("lock")
        return self._held()


@dataclass
class FakeStamp:
    calls: list[str]
    stamp: Stamp | None = None
    written: list[Stamp] = field(default_factory=list)

    def read(self) -> Stamp | None:
        return self.stamp

    def write(self, stamp: Stamp) -> None:
        self.calls.append("stamp.write")
        self.written.append(stamp)
        self.stamp = stamp


def recording_rules(
    calls: list[str], decisions: Sequence[Decision]
) -> Callable[..., list[Decision]]:
    def rules(snapshot: Snapshot, ctx: object) -> list[Decision]:
        calls.append("rules")
        return list(decisions)

    return rules


def grouping_components(
    calls: list[str], groups: Sequence[Sequence[str]]
) -> Callable[[Snapshot, Sequence[Decision]], list[ComponentPlan]]:
    """Fixed membership; status is the first non-ELIGIBLE member verdict."""

    def components(
        snapshot: Snapshot, decisions: Sequence[Decision]
    ) -> list[ComponentPlan]:
        calls.append("components")
        by_name = {d.full_name: d for d in decisions}
        plans: list[ComponentPlan] = []
        for group in groups:
            members = tuple(sorted(group))
            verdicts = [by_name[m].verdict for m in members]
            status = next((v for v in verdicts if v is not Verdict.ELIGIBLE), None)
            plans.append(
                ComponentPlan(
                    members=members,
                    status=status or Verdict.ELIGIBLE,
                    blocked_by=None,
                    reason="fake",
                )
            )
        return plans

    return components


def recording_reconcile(
    calls: list[str],
) -> Callable[[Snapshot, HoldStorePort], list[str]]:
    def reconcile(snapshot: Snapshot, holds: HoldStorePort) -> list[str]:
        calls.append("reconcile")
        return []

    return reconcile
