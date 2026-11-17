"""Pure join of brew's JSON into the Snapshot every rule reads. No I/O."""

from collections.abc import Mapping, Sequence
from typing import Any

from morning_brew.domain import BrewParseError, Candidate, Kind, Snapshot
from morning_brew.versions import classify_bump, newest_installed


def _short(name: str) -> str:
    return name.rsplit("/", 1)[-1]


def _newest_entry(entry: Mapping[str, Any], newest: str | None) -> Mapping[str, Any]:
    installed = entry.get("installed") or []
    if not installed:
        return {}
    for item in installed:
        if item.get("version") == newest:
            return item
    return installed[-1]


def _deps_of(entry: Mapping[str, Any]) -> tuple[str, ...]:
    item = _newest_entry(
        entry,
        newest_installed([i.get("version", "") for i in entry.get("installed") or []]),
    )
    deps = item.get("runtime_dependencies") or []
    return tuple(d["full_name"] for d in deps if d.get("full_name"))


def _has_arm64_bottle(entry: Mapping[str, Any]) -> bool:
    files = ((entry.get("bottle") or {}).get("stable") or {}).get("files") or {}
    return any(key.startswith("arm64_") or key == "all" for key in files)


def build_snapshot(
    outdated: Mapping,
    info: Mapping,
    leaves: Sequence[str],
    services: Sequence[Mapping],
    brew_version: str,
) -> Snapshot:
    try:
        formulae = {f["full_name"]: f for f in info["formulae"]}
        casks = {c["token"]: c for c in info["casks"]}
        outdated_formulae = outdated["formulae"]
        outdated_casks = outdated["casks"]
    except (KeyError, TypeError) as exc:
        raise BrewParseError(f"unexpected brew JSON shape: {exc!r}") from exc

    dep_graph = {name: _deps_of(entry) for name, entry in formulae.items()}
    dependents: dict[str, int] = {}
    for deps in dep_graph.values():
        for dep in set(deps):
            dependents[dep] = dependents.get(dep, 0) + 1

    status_by_short = {s["name"]: s["status"] for s in services if "name" in s}
    service_map = {
        name: status_by_short[_short(name)]
        for name in formulae
        if _short(name) in status_by_short
    }
    leaf_set = set(leaves)

    candidates: list[Candidate] = []
    unjoinable: list[str] = []

    for item in outdated_formulae:
        name = item["name"]
        entry = formulae.get(name)
        if entry is None:
            unjoinable.append(name)
            continue
        installed = newest_installed(item.get("installed_versions") or [])
        current = item["current_version"]
        candidates.append(
            Candidate(
                full_name=name,
                kind=Kind.FORMULA,
                installed=installed,
                current=current,
                bump=classify_bump(installed or "", current),
                is_leaf=name in leaf_set or _short(name) in leaf_set,
                dependents=dependents.get(name, 0),
                runtime_deps=dep_graph[name],
                tap=entry.get("tap") or "",
                has_arm64_bottle=_has_arm64_bottle(entry),
                caveats=entry.get("caveats"),
                deprecated=bool(entry.get("deprecated")),
                disabled=bool(entry.get("disabled")),
                pinned=bool(item.get("pinned")) or bool(entry.get("pinned")),
                service_status=service_map.get(name),
            )
        )

    for item in outdated_casks:
        name = item["name"]
        entry = casks.get(name)
        if entry is None:
            unjoinable.append(name)
            continue
        installed = newest_installed(item.get("installed_versions") or [])
        current = item["current_version"]
        candidates.append(
            Candidate(
                full_name=name,
                kind=Kind.CASK,
                installed=installed,
                current=current,
                bump=classify_bump(installed or "", current),
                is_leaf=True,
                dependents=0,
                runtime_deps=(),
                tap=entry.get("tap") or "",
                has_arm64_bottle=True,
                caveats=entry.get("caveats"),
                deprecated=bool(entry.get("deprecated")),
                disabled=bool(entry.get("disabled")),
                pinned=bool(item.get("pinned")) or bool(entry.get("pinned")),
                service_status=None,
            )
        )

    return Snapshot(
        brew_version=brew_version,
        candidates=tuple(candidates),
        unjoinable=tuple(unjoinable),
        dep_graph=dep_graph,
        services=service_map,
        installed_names=frozenset(formulae),
    )
