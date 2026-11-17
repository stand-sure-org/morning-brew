import copy
from typing import Any

from conftest import fixture_text, load_json
from morning_brew.brew.snapshot import build_snapshot
from morning_brew.domain import Bump, Candidate, Kind, Snapshot


def fixture_inputs() -> dict[str, Any]:
    return {
        "outdated": load_json("outdated.json"),
        "info": load_json("info.json"),
        "leaves": fixture_text("leaves.txt").split(),
        "services": load_json("services.json"),
        "brew_version": "7.0.14",
    }


def by_name(s: Snapshot, name: str) -> Candidate:
    return next(c for c in s.candidates if c.full_name == name)


def _formula(inputs: dict[str, Any], name: str) -> dict[str, Any]:
    return next(f for f in inputs["info"]["formulae"] if f["full_name"] == name)


def test_tap_formula_joins_on_full_name():
    s = build_snapshot(**fixture_inputs())
    v = by_name(s, "vectordotdev/brew/vector")
    assert v.tap == "vectordotdev/brew"
    assert v.service_status == "none"
    assert v.bump is Bump.ZERO_MAJOR_MINOR
    assert "vectordotdev/brew/vector" in s.services


def test_dependents_inverted_from_runtime_dependencies():
    s = build_snapshot(**fixture_inputs())
    assert by_name(s, "aws-c-auth").dependents >= 2
    assert by_name(s, "awscli").is_leaf
    assert by_name(s, "aws-c-auth").is_leaf is False
    assert all(
        name in s.dep_graph[dependent]
        for name in ("aws-c-auth",)
        for dependent in s.dep_graph
        if name in s.dep_graph[dependent]
    )
    assert by_name(s, "aws-c-auth").dependents == sum(
        "aws-c-auth" in deps for deps in s.dep_graph.values()
    )


def test_source_built_has_no_arm64_bottle():
    s = build_snapshot(**fixture_inputs())
    assert (
        _formula(fixture_inputs(), "jandedobbeleer/oh-my-posh/oh-my-posh")["bottle"]
        == {}
    )
    assert by_name(s, "jandedobbeleer/oh-my-posh/oh-my-posh").has_arm64_bottle is False
    assert by_name(s, "awscli").has_arm64_bottle is True


def test_unjoinable_is_reported_not_dropped():
    inputs = fixture_inputs()
    inputs["outdated"] = copy.deepcopy(inputs["outdated"])
    inputs["outdated"]["formulae"].append(
        {
            "name": "ghost",
            "installed_versions": ["1.0"],
            "current_version": "1.1",
            "pinned": False,
            "pinned_version": None,
        }
    )
    s = build_snapshot(**inputs)
    assert s.unjoinable == ("ghost",)
    assert all(c.full_name != "ghost" for c in s.candidates)


def test_multiple_and_empty_installed_versions():
    inputs = fixture_inputs()
    inputs["outdated"] = {
        "formulae": [
            {
                "name": "x",
                "installed_versions": ["1.0.0", "1.2.0", "1.1.0"],
                "current_version": "1.3.0",
                "pinned": False,
            },
            {
                "name": "y",
                "installed_versions": [],
                "current_version": "2.0.0",
                "pinned": False,
            },
        ],
        "casks": [],
    }
    template = _formula(inputs, "awscli")
    x = copy.deepcopy(template) | {"name": "x", "full_name": "x"}
    y = copy.deepcopy(template) | {"name": "y", "full_name": "y"}
    inputs["info"] = {"formulae": [x, y], "casks": []}
    s_multi = build_snapshot(**inputs)
    assert by_name(s_multi, "x").installed == "1.2.0"
    assert by_name(s_multi, "x").bump is Bump.MINOR
    y_c = by_name(s_multi, "y")
    assert y_c.installed is None
    assert y_c.bump is Bump.UNPARSEABLE


def test_casks_join_on_token():
    s = build_snapshot(**fixture_inputs())
    c = by_name(s, "antigravity-cli")
    assert c.kind is Kind.CASK
    assert c.runtime_deps == ()
    assert c.dependents == 0
    assert c.is_leaf is True
    assert c.service_status is None
    assert c.has_arm64_bottle is True
    assert c.caveats is not None
    assert "antigravity-cli" not in s.unjoinable


def test_pinned_and_caveats_flow_through():
    s = build_snapshot(**fixture_inputs())
    assert by_name(s, "redis").pinned is True
    assert by_name(s, "awscli").pinned is False
    info = fixture_inputs()["info"]
    with_caveats = next(
        f
        for f in info["formulae"]
        if f["caveats"] and f["full_name"] in {c.full_name for c in s.candidates}
    )
    assert by_name(s, with_caveats["full_name"]).caveats == with_caveats["caveats"]


def test_real_fixture_has_no_unjoinable():
    inputs = fixture_inputs()
    s = build_snapshot(**inputs)
    assert s.unjoinable == ()
    expected = len(inputs["outdated"]["formulae"]) + len(inputs["outdated"]["casks"])
    assert len(s.candidates) == expected
    for deps in s.dep_graph.values():
        assert set(deps) <= set(s.dep_graph)
