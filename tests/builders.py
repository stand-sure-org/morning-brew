from dataclasses import replace
from typing import Any

from morning_brew.domain import Bump, Candidate, Kind, Snapshot


def make_candidate(full_name: str = "pkg", **overrides: Any) -> Candidate:
    base = Candidate(
        full_name=full_name,
        kind=Kind.FORMULA,
        installed="1.0.0",
        current="1.0.1",
        bump=Bump.PATCH,
        is_leaf=True,
        dependents=0,
        runtime_deps=(),
        tap="homebrew/core",
        has_arm64_bottle=True,
        caveats=None,
        deprecated=False,
        disabled=False,
        pinned=False,
        service_status=None,
    )
    return replace(base, **overrides)


def make_snapshot(*candidates: Candidate, **overrides: Any) -> Snapshot:
    base = Snapshot(
        brew_version="7.0.14",
        candidates=tuple(candidates),
        unjoinable=(),
        dep_graph={c.full_name: c.runtime_deps for c in candidates},
        services={},
        installed_names=frozenset(c.full_name for c in candidates),
    )
    return replace(base, **overrides)
