"""Domain types shared by the orchestrator and every adapter."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol


class Kind(StrEnum):
    FORMULA = "formula"
    CASK = "cask"


class Bump(StrEnum):
    REVISION = "revision"
    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"
    ZERO_MAJOR_MINOR = "zero_major_minor"
    UNPARSEABLE = "unparseable"


class Verdict(StrEnum):
    ELIGIBLE = "eligible"
    HOLD = "hold"
    SKIP = "skip"
    DEFER = "defer"
    REPORT = "report"
    GREY = "grey"


class HoldKind(StrEnum):
    DECIDED = "decided"
    TRANSIENT = "transient"


@dataclass(frozen=True, slots=True)
class Candidate:
    full_name: str
    kind: Kind
    installed: str | None
    current: str
    bump: Bump
    is_leaf: bool
    dependents: int
    runtime_deps: tuple[str, ...]
    tap: str
    has_arm64_bottle: bool
    caveats: str | None
    deprecated: bool
    disabled: bool
    pinned: bool
    service_status: str | None


@dataclass(frozen=True, slots=True)
class Snapshot:
    brew_version: str
    candidates: tuple[Candidate, ...]
    unjoinable: tuple[str, ...]
    dep_graph: Mapping[str, tuple[str, ...]]
    services: Mapping[str, str]
    installed_names: frozenset[str]


@dataclass(frozen=True, slots=True)
class Decision:
    full_name: str
    verdict: Verdict
    rule_id: str
    reason: str
    hold_kind: HoldKind | None = None


@dataclass(frozen=True, slots=True)
class ComponentPlan:
    members: tuple[str, ...]
    status: Verdict
    blocked_by: str | None
    reason: str


@dataclass(frozen=True)
class RuleContext:
    live: frozenset[str]
    trusted_taps: frozenset[str]
    self_deps: frozenset[str]
    now: datetime
    min_age_days: int
    first_seen: Callable[[str, str], datetime | None]
    caveat_hash: Callable[[str], str | None]


@dataclass(frozen=True)
class TriageRequest:
    full_name: str
    installed: str | None
    current: str
    bump: Bump
    caveats: str | None
    deprecated: bool
    tap: str
    dependents: int
    is_leaf: bool


@dataclass(frozen=True)
class TriageOutcome:
    kind: Literal["upgrade", "hold", "transient"]
    reason: str
    stage: str
    local_flagged: bool = False
    disagreement: bool = False
    would_be: Literal["upgrade", "hold"] | None = None
    reasoning: str | None = None


@dataclass(frozen=True)
class UpgradeResult:
    ok: bool
    stderr_tail: str


@dataclass(frozen=True)
class Hold:
    name: str
    kind: HoldKind
    held_at_version: str
    reason: str
    first_held: datetime
    blocked_by: str | None = None
    model_reasoning: str | None = None
    skipped_for_version: str | None = None


@dataclass(frozen=True)
class Stamp:
    last_full_success: datetime | None
    last_attempt: datetime | None
    quality: Literal["full", "degraded"]
    full_runs: int
    first_attempt: datetime | None = None  # [R16] dead-man reference before any success


@dataclass(frozen=True)
class AuditEvent:
    run_id: str
    ts: datetime
    brew_version: str
    stage: str
    package: str | None = None
    rule_id: str | None = None
    verdict: str | None = None
    action_result: str | None = None
    models: tuple[tuple[str, str | None], ...] = ()
    booleans: Mapping[str, bool] | None = None
    reasoning: str | None = None
    # [R3/R4] ",".join(sorted(members)) for multi-member components;
    # None for singletons and casks.
    component: str | None = None
    # [R20] HoldKind value of the recorded decision when the verdict is hold.
    hold_kind: str | None = None


@dataclass(frozen=True)
class Summary:
    run_id: str
    quality: Literal["full", "degraded"]
    upgraded: tuple[str, ...]
    failed: tuple[str, ...]
    decided_holds: int
    oldest_decided_hold_days: int | None
    blocked_by_dep: int
    transient: int
    self_dep_commands: tuple[str, ...]
    disagreements: tuple[str, ...]
    live_old_binary: tuple[str, ...]
    anomalies: tuple[str, ...]
    dead_man: bool
    degraded_reasons: tuple[str, ...]


@dataclass(frozen=True)
class RunResult:
    exit_code: int
    summary: Summary | None


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_TEMPFAIL = 75


class BrewUpdateError(Exception):
    """`brew update` failed; abort before any mutation (exit 75)."""


class BrewParseError(Exception):
    """brew produced output the snapshot parser could not read."""


class BrewBusyError(Exception):
    """Another brew process holds Homebrew's own lock."""


class InvalidPackageName(ValueError):
    """A package name failed validation and must never reach argv."""


class LockHeld(Exception):
    """Another morning-brew run holds the run lock."""


class StateUnwritable(Exception):
    """The state directory cannot be written."""


class ModelUnavailable(Exception):
    """A triage model could not be reached."""


class ModelInvalidOutput(Exception):
    """A triage model answered outside its schema."""


class ModelTransient(Exception):
    """A triage model failed in a retryable way (timeout, rate limit, refusal)."""


class SecretUnavailable(Exception):
    """The API key could not be fetched from fnox."""


class GateNotPassed(Exception):
    """The ship gate has not been passed; live chain mode is refused."""


class UnlabeledCases(Exception):
    """The golden corpus has cases the owner has not labelled yet."""


class InstallError(Exception):
    """The LaunchAgent cannot be installed (venv missing or launchctl failed)."""


class ConfigError(Exception):
    """The config file is invalid."""


class ConfigPermissionError(ConfigError):
    """The config file is group- or world-writable."""


def request_from(c: Candidate) -> TriageRequest:
    return TriageRequest(
        full_name=c.full_name,
        installed=c.installed,
        current=c.current,
        bump=c.bump,
        caveats=c.caveats,
        deprecated=c.deprecated,
        tap=c.tap,
        dependents=c.dependents,
        is_leaf=c.is_leaf,
    )


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    """Runs argv with a timeout; ``input`` (when given) is fed to stdin."""

    def __call__(
        self, argv: Sequence[str], timeout: float, input: str | None = None
    ) -> CommandResult: ...
