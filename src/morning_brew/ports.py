"""Ports the orchestrator consumes (consumer-defined, ISP)."""

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from morning_brew.domain import (
    AuditEvent,
    ComponentPlan,
    Decision,
    Hold,
    RuleContext,
    Snapshot,
    Stamp,
    Summary,
    TriageOutcome,
    TriageRequest,
    UpgradeResult,
)


class BrewPort(Protocol):
    def update(self) -> None: ...
    def snapshot(self) -> Snapshot: ...
    def upgrade(self, members: Sequence[str]) -> UpgradeResult: ...
    def dry_run_closure(self, members: Sequence[str]) -> frozenset[str]: ...
    def cleanup(self, packages: Sequence[str]) -> None: ...


class LiveProcessPort(Protocol):
    def live_set(self, snapshot: Snapshot) -> frozenset[str]: ...


class TriagePort(Protocol):
    def triage(self, request: TriageRequest) -> TriageOutcome: ...


class HoldStorePort(Protocol):
    def ensure_writable(self) -> None: ...
    def get(self, name: str) -> Hold | None: ...
    def put(self, hold: Hold) -> None: ...
    def release(self, name: str) -> None: ...
    def list_all(self) -> list[Hold]: ...
    def mark_skipped(self, name: str, version: str) -> None: ...


class SeenStorePort(Protocol):
    def record_first_seen(self, name: str, version: str, now: datetime) -> None: ...
    def first_seen(self, name: str, version: str) -> datetime | None: ...
    def caveat_hash(self, name: str) -> str | None: ...
    def record_caveat_hash(self, name: str, h: str) -> None: ...
    def record_upgrade(self, run_seq: int, names: Sequence[str]) -> None: ...
    def due_for_cleanup(self, current_seq: int, runs: int) -> list[str]: ...


class AuditPort(Protocol):
    def record(self, event: AuditEvent) -> None: ...
    def events_for(self, package: str) -> list[AuditEvent]: ...


class NotifierPort(Protocol):
    def notify(self, summary: Summary, *, urgent: bool) -> None: ...


class StatusPort(Protocol):
    def write(self, summary: Summary) -> None: ...
    def read(self) -> Summary | None: ...


class LockPort(Protocol):
    def acquire(self) -> AbstractContextManager[None]: ...


class StampPort(Protocol):
    def read(self) -> Stamp | None: ...
    def write(self, stamp: Stamp) -> None: ...


RulesFn = Callable[[Snapshot, RuleContext], list[Decision]]
ComponentsFn = Callable[[Snapshot, Sequence[Decision]], list[ComponentPlan]]
ReconcileFn = Callable[[Snapshot, HoldStorePort], list[str]]  # [R1] real impl T12


@dataclass
class Ports:
    brew: BrewPort
    live: LiveProcessPort
    rules: RulesFn
    components: ComponentsFn
    reconcile: ReconcileFn
    triage: TriagePort
    holds: HoldStorePort
    seen: SeenStorePort
    audit: AuditPort
    notifier: NotifierPort
    status: StatusPort
    lock: LockPort
    stamp: StampPort
    clock: Callable[[], datetime]
    self_deps: frozenset[str] = field(default_factory=frozenset)  # [R13]
