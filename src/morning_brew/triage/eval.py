"""Golden set, eval runner and ship gate (FR-057).

The gate is defined before the triage chain it measures: live chain mode is
refused until an eval over the labelled corpus recorded zero false approves on
every case expected to be held.
"""

import hashlib
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from importlib import resources
from pathlib import Path
from typing import Literal

from morning_brew.config import Config
from morning_brew.domain import (
    AuditEvent,
    Bump,
    GateNotPassed,
    TriageOutcome,
    TriageRequest,
    UnlabeledCases,
)
from morning_brew.ports import AuditPort, TriagePort
from morning_brew.store.atomic import read_json_or_none, write_json_atomic

log = logging.getLogger(__name__)

_CURRENT = {
    Bump.REVISION: "1.0.0_1",
    Bump.PATCH: "1.0.1",
    Bump.MINOR: "1.1.0",
    Bump.MAJOR: "2.0.0",
    Bump.ZERO_MAJOR_MINOR: "1.1.0",
    Bump.UNPARSEABLE: "unparseable",
}


@dataclass(frozen=True)
class GoldenCase:
    id: str
    subset: Literal["real", "actionable", "injection"]
    caveat: str
    expected: Literal["upgrade", "hold"] | None
    bump: Bump
    tap: str


@dataclass(frozen=True)
class EvalReport:
    total: int
    false_approves: tuple[str, ...]
    missed_upgrades: tuple[str, ...]
    inconclusive: tuple[str, ...] = ()


def load_cases(path: Path | None = None) -> list[GoldenCase]:
    if path is None:
        text = (
            resources.files("morning_brew.triage")
            .joinpath("golden/cases.jsonl")
            .read_text(encoding="utf-8")
        )
    else:
        text = path.read_text(encoding="utf-8")
    cases = []
    for line in text.splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        cases.append(
            GoldenCase(
                id=d["id"],
                subset=d["subset"],
                caveat=d["caveat"],
                expected=d["expected"],
                bump=Bump(d["bump"]),
                tap=d["tap"],
            )
        )
    return cases


def effective_verdict(o: TriageOutcome) -> str:
    """What the chain would do: shadow outcomes carry it in ``would_be``."""
    if "upgrade" in (o.kind, o.would_be):
        return "upgrade"
    return o.would_be or o.kind


def run_eval(
    triage: TriagePort,
    cases: Sequence[GoldenCase],
    audit: AuditPort,
    run_id: str,
    now: datetime | None = None,
) -> EvalReport:
    unlabeled = [c.id for c in cases if c.expected is None]
    if unlabeled:
        raise UnlabeledCases(", ".join(unlabeled))
    ts = now or datetime.now().astimezone()
    false_approves: list[str] = []
    missed: list[str] = []
    inconclusive: list[str] = []
    for c in cases:
        request = TriageRequest(
            full_name=f"golden/{c.id}",
            installed="1.0.0",
            current=_CURRENT[c.bump],
            bump=c.bump,
            caveats=c.caveat,
            deprecated=False,
            tap=c.tap,
            dependents=0,
            is_leaf=True,
        )
        outcome = triage.triage(request)
        verdict = effective_verdict(outcome)
        audit.record(
            AuditEvent(
                run_id=run_id,
                ts=ts,
                brew_version="eval",
                stage="eval",
                package=request.full_name,
                verdict=verdict,
                reasoning=outcome.reasoning,
            )
        )
        if verdict == "transient":
            inconclusive.append(c.id)
            if c.expected == "upgrade":
                missed.append(c.id)
        elif c.expected == "hold" and verdict == "upgrade":
            false_approves.append(c.id)
        elif c.expected == "upgrade" and verdict != "upgrade":
            missed.append(c.id)
    return EvalReport(
        len(cases), tuple(false_approves), tuple(missed), tuple(inconclusive)
    )


def gate_passed(report: EvalReport, cases: Sequence[GoldenCase]) -> bool:
    """Zero false approves over the complete shipped, fully labelled corpus.

    ``cases`` must equal the shipped corpus exactly (ids, labels, text).

    Fails closed: a subset or different corpus, unlabeled cases, a report that
    does not cover the cases, any false approve, or any inconclusive (model
    unavailable) case never passes.
    """
    if list(cases) != load_cases():
        return False
    if any(c.expected is None for c in cases) or report.total != len(cases):
        return False
    return not report.false_approves and not report.inconclusive


def eval_exit_code(report: EvalReport, cases: Sequence[GoldenCase]) -> int:
    return 0 if gate_passed(report, cases) else 1


def corpus_sha256() -> str:
    data = (
        resources.files("morning_brew.triage")
        .joinpath("golden/cases.jsonl")
        .read_bytes()
    )
    return hashlib.sha256(data).hexdigest()


class GateStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def record_eval(
        self, triage: TriagePort, audit: AuditPort, run_id: str, now: datetime
    ) -> EvalReport:
        """Run the shipped corpus and record the result.

        The only way to write the gate: a caller-built report is never trusted.
        """
        cases = load_cases()
        report = run_eval(triage, cases, audit, run_id, now)
        write_json_atomic(
            self._path,
            {
                "passed": gate_passed(report, cases),
                "ts": now.isoformat(),
                "total": report.total,
                "false_approves": len(report.false_approves),
                "inconclusive": len(report.inconclusive),
                "corpus_sha256": corpus_sha256(),
            },
        )
        return report

    def passed(self) -> bool:
        data = read_json_or_none(self._path)
        return (
            isinstance(data, dict)
            and data.get("passed") is True
            and data.get("corpus_sha256") == corpus_sha256()
        )


def require_gate(config: Config, gate: GateStore) -> None:
    if config.chain_mode == "live" and not gate.passed():
        raise GateNotPassed("live chain mode requires a recorded passing eval")
