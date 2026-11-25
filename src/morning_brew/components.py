"""Package-name validation and connected components of outdated formulae.

Brew upgrades a package's outdated dependents and dependencies together, so
the decision is per undirected component: one held member holds the whole.
"""

import re
from collections.abc import Iterable, Sequence

from morning_brew.domain import (
    ComponentPlan,
    Decision,
    InvalidPackageName,
    Kind,
    Snapshot,
    Verdict,
)

_NAME = re.compile(r"[A-Za-z0-9@._+/-]+", re.ASCII)

# Highest priority first. REPORT is folded into DEFER for multi-member plans.
_PRECEDENCE = (Verdict.HOLD, Verdict.SKIP, Verdict.DEFER, Verdict.GREY)


def validate_name(name: str) -> bool:
    return (
        _NAME.fullmatch(name) is not None
        and not name.startswith("-")
        and ".." not in name
    )


def require_valid(names: Iterable[str]) -> None:
    for name in names:
        if not validate_name(name):
            raise InvalidPackageName(f"invalid package name: {name!r}")


def _find(parent: dict[str, str], x: str) -> str:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def plan_components(
    snapshot: Snapshot, decisions: Sequence[Decision]
) -> list[ComponentPlan]:
    by_name = {d.full_name: d for d in decisions}
    formulae = {c.full_name: c for c in snapshot.candidates if c.kind is Kind.FORMULA}
    casks = sorted(c.full_name for c in snapshot.candidates if c.kind is Kind.CASK)

    parent = {n: n for n in formulae}
    for name, cand in formulae.items():
        for dep in cand.runtime_deps:
            if dep in parent:
                parent[_find(parent, dep)] = _find(parent, name)

    groups: dict[str, list[str]] = {}
    for name in formulae:
        groups.setdefault(_find(parent, name), []).append(name)

    plans = [_plan(tuple(sorted(m)), by_name) for m in groups.values()]
    plans.extend(_plan((n,), by_name) for n in casks)
    plans.sort(key=lambda p: p.members[0])
    return plans


def _verdict(d: Decision | None, multi: bool) -> Verdict:
    if d is None:
        return Verdict.HOLD  # fail closed: no decision never upgrades
    if d.verdict is Verdict.REPORT and multi:
        return Verdict.DEFER
    return d.verdict


def _plan(members: tuple[str, ...], by_name: dict[str, Decision]) -> ComponentPlan:
    multi = len(members) > 1
    verdicts = {m: _verdict(by_name.get(m), multi) for m in members}
    for status in (*_PRECEDENCE, Verdict.REPORT):
        blocker = next((m for m in members if verdicts[m] is status), None)
        if blocker is None:
            continue
        if status is Verdict.GREY:
            return ComponentPlan(members, status, None, f"pending: {blocker}")
        d = by_name.get(blocker)
        reason = d.reason if d else f"no decision for {blocker}"
        return ComponentPlan(members, status, blocker, reason)
    return ComponentPlan(members, Verdict.ELIGIBLE, None, "all members eligible")
