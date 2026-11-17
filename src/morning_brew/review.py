"""Interactive review of decided holds (FR-060, FR-080).

The human is the decider here: approving a hold upgrades its whole component
in one explicit ``brew upgrade -- ...`` call; skipping records the version so
it is never auto-upgraded (R14); deferring does nothing. The tool never pins.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from morning_brew.domain import (
    AuditEvent,
    BrewBusyError,
    Decision,
    Hold,
    HoldKind,
    InvalidPackageName,
    Kind,
    Snapshot,
    Verdict,
)
from morning_brew.ports import (
    AuditPort,
    BrewPort,
    ComponentsFn,
    HoldStorePort,
    LiveProcessPort,
)
from morning_brew.sanitize import tail_redacted
from morning_brew.textsafe import strip_control

_MAX_REASONING = 500
_MAX_ATTEMPTS = 3
_PROMPT = "[a]pprove / [s]kip this version / [d]efer? "


@dataclass(frozen=True)
class ReviewResult:
    approved: tuple[str, ...]
    skipped: tuple[str, ...]
    deferred: tuple[str, ...]


class _Stop(Exception):
    """The input ended; stop the review cleanly."""


def review(
    holds: HoldStorePort,
    brew: BrewPort,
    components: ComponentsFn,
    live: LiveProcessPort,
    audit: AuditPort,
    ask: Callable[[str], str],
    say: Callable[[str], None],
    now: datetime,
    run_id: str,
    self_deps: frozenset[str] = frozenset(),
) -> ReviewResult:
    approved: list[str] = []
    skipped: list[str] = []
    deferred: list[str] = []
    brew_version = ""

    def record(name: str, verdict: str, result: str | None = None) -> None:
        audit.record(
            AuditEvent(
                run_id=run_id,
                ts=now,
                brew_version=brew_version,
                stage="review",
                package=name,
                rule_id=None,
                verdict=verdict,
                action_result=result,
            )
        )

    def prompt(question: str) -> str:
        try:
            return ask(question).strip().lower()
        except EOFError:
            raise _Stop from None

    def approve(h: Hold) -> bool:
        nonlocal brew_version
        snapshot = brew.snapshot()
        brew_version = snapshot.brew_version
        outdated = {c.full_name for c in snapshot.candidates}
        if h.name not in outdated:
            holds.release(h.name)
            say(f"{h.name}: no longer outdated; hold released.")
            record(h.name, "approved", "no longer outdated")
            return False
        members = _component_of(h.name, snapshot, components)
        if members is None:
            say(f"{h.name}: not an upgradable formula; left for you.")
            record(h.name, "deferred", "not a formula")
            deferred.append(h.name)
            return False
        if self_deps.intersection(members):
            say(
                f"{h.name}: the component contains a self-dependency of this "
                "tool; upgrade it yourself:"
            )
            say("  brew upgrade -- " + " ".join(members))
            record(h.name, "deferred", "self-dependency; manual command shown")
            deferred.append(h.name)
            return False
        live_members = sorted(set(members) & live.live_set(snapshot))
        if live_members:
            say(f"Running processes use: {', '.join(live_members)}.")
            if prompt("Type 'yes' to upgrade anyway: ") != "yes":
                say("Not confirmed; nothing upgraded.")
                record(h.name, "deferred", "live process not confirmed")
                deferred.append(h.name)
                return False
        try:
            result = brew.upgrade(members)
        except BrewBusyError:
            say("brew busy, try later")
            record(h.name, "deferred", "brew busy")
            deferred.append(h.name)
            return False
        except InvalidPackageName:
            say(f"{h.name}: refused, invalid package name in component.")
            record(h.name, "deferred", "invalid package name")
            deferred.append(h.name)
            return False
        if not result.ok:
            say(f"{h.name}: upgrade failed; hold kept.")
            say(strip_control(tail_redacted(result.stderr_tail, 500)))
            record(h.name, "approved", "upgrade failed")
            return False
        for member in members:
            if holds.get(member) is not None:
                holds.release(member)
        say(f"Upgraded: {' '.join(members)}")
        record(h.name, "approved", "upgraded")
        approved.append(h.name)
        return True

    pending = [
        h
        for h in holds.list_all()
        if h.kind is HoldKind.DECIDED and h.skipped_for_version != h.held_at_version
    ]
    try:
        for h in pending:
            _show(h, now, say)
            for _ in range(_MAX_ATTEMPTS):
                answer = prompt(_PROMPT)
                if answer == "a":
                    approve(h)
                    break
                if answer == "s":
                    holds.mark_skipped(h.name, h.held_at_version)
                    record(h.name, "skipped", h.held_at_version)
                    skipped.append(h.name)
                    break
                if answer == "d":
                    record(h.name, "deferred")
                    deferred.append(h.name)
                    break
                say("Please answer a, s or d.")
            else:
                say(f"{h.name}: no valid answer; deferred.")
                record(h.name, "deferred", "no valid answer")
                deferred.append(h.name)
    except _Stop:
        say("Input ended; review stopped.")
    return ReviewResult(tuple(approved), tuple(skipped), tuple(deferred))


def _component_of(
    name: str, snapshot: Snapshot, components: ComponentsFn
) -> tuple[str, ...] | None:
    if any(c.full_name == name and c.kind is Kind.CASK for c in snapshot.candidates):
        return None
    decisions = [
        Decision(c.full_name, Verdict.ELIGIBLE, "review", "approved by owner")
        for c in snapshot.candidates
    ]
    for plan in components(snapshot, decisions):
        if name in plan.members:
            return tuple(sorted(plan.members))
    return None


def _show(h: Hold, now: datetime, say: Callable[[str], None]) -> None:
    age = max((now - h.first_held).days, 0)
    say(f"{strip_control(h.name)} held at {strip_control(h.held_at_version)}")
    say(f"  reason: {strip_control(h.reason)}")
    say(f"  age: {age} days")
    if h.model_reasoning:
        text = strip_control(h.model_reasoning)[:_MAX_REASONING]
        say(f"  model reasoning (derived from untrusted text): {text}")
