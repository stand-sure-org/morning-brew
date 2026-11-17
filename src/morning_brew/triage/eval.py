"""Golden set, eval runner and ship gate (FR-057).

The gate is defined before the triage chain it measures: live chain mode is
refused until an eval over the labelled corpus recorded zero false approves on
every case expected to be held.
"""

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
        if c.expected == "hold" and verdict == "upgrade":
            false_approves.append(c.id)
        elif c.expected == "upgrade" and verdict != "upgrade":
            missed.append(c.id)
    return EvalReport(len(cases), tuple(false_approves), tuple(missed))


def gate_passed(report: EvalReport, cases: Sequence[GoldenCase]) -> bool:
    """Zero false approves on every case expected to be held.

    Fails closed: unlabeled cases, or a report that does not cover the cases,
    never pass.
    """
    if any(c.expected is None for c in cases) or report.total != len(cases):
        return False
    held = {c.id for c in cases if c.expected == "hold"}
    return not held.intersection(report.false_approves)


def eval_exit_code(report: EvalReport, cases: Sequence[GoldenCase]) -> int:
    return 0 if gate_passed(report, cases) else 1


class GateStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def record(
        self,
        report: EvalReport,
        now: datetime,
        cases: Sequence[GoldenCase] | None = None,
    ) -> None:
        # run_eval only reports false approves for cases expected to be held,
        # so without the corpus "no false approves over a non-empty run" is
        # the same test as gate_passed.
        passed = (
            gate_passed(report, cases)
            if cases is not None
            else report.total > 0 and not report.false_approves
        )
        write_json_atomic(
            self._path,
            {
                "passed": passed,
                "ts": now.isoformat(),
                "total": report.total,
                "false_approves": len(report.false_approves),
            },
        )

    def passed(self) -> bool:
        data = read_json_or_none(self._path)
        return isinstance(data, dict) and data.get("passed") is True


def require_gate(config: Config, gate: GateStore) -> None:
    if config.chain_mode == "live" and not gate.passed():
        raise GateNotPassed("live chain mode requires a recorded passing eval")
