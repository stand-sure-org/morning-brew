from datetime import timedelta

import pytest

from builders import NOW, make_candidate, make_ctx, make_snapshot
from morning_brew.brew.snapshot import build_snapshot
from morning_brew.caveats import caveat_hash
from morning_brew.domain import (
    Bump,
    Decision,
    HoldKind,
    Kind,
    TriageOutcome,
    Verdict,
)
from morning_brew.rules import RULES, apply_outcome, evaluate, evaluate_all
from test_snapshot import fixture_inputs

CTX = make_ctx(
    self_deps=frozenset({"fnox"}),
    live=frozenset({"vectordotdev/brew/vector"}),
)


def test_rule_ids_in_order():
    assert [r.id for r in RULES] == [f"R{i:02d}" for i in range(1, 13)]


@pytest.mark.parametrize(
    "overrides,verdict,rule",
    [
        ({"full_name": "-x"}, Verdict.HOLD, "R01"),
        ({"pinned": True}, Verdict.SKIP, "R02"),
        ({"kind": Kind.CASK}, Verdict.REPORT, "R03"),
        ({"disabled": True}, Verdict.HOLD, "R04"),
        ({"full_name": "fnox"}, Verdict.REPORT, "R05"),
        ({"tap": "jandedobbeleer/oh-my-posh"}, Verdict.HOLD, "R06"),
        ({"has_arm64_bottle": False}, Verdict.HOLD, "R07"),
        ({"full_name": "vectordotdev/brew/vector"}, Verdict.HOLD, "R08"),
        ({"bump": Bump.MAJOR}, Verdict.HOLD, "R09"),
        ({"bump": Bump.UNPARSEABLE}, Verdict.HOLD, "R09"),
        ({"deprecated": True}, Verdict.HOLD, "R09"),
        ({"is_leaf": False, "dependents": 10}, Verdict.HOLD, "R09"),
        ({"bump": Bump.ZERO_MAJOR_MINOR}, Verdict.GREY, "R11"),
        ({"caveats": "changed text"}, Verdict.GREY, "R11"),
        ({}, Verdict.ELIGIBLE, "R12"),
    ],
)
def test_rule(overrides, verdict, rule):
    d = evaluate(make_candidate(**overrides), CTX)
    assert (d.verdict, d.rule_id) == (verdict, rule)
    assert rule in d.reason


def test_hold_kinds():
    assert evaluate(make_candidate("-x"), CTX).hold_kind is HoldKind.TRANSIENT
    assert evaluate(make_candidate(disabled=True), CTX).hold_kind is HoldKind.DECIDED
    assert evaluate(make_candidate(), CTX).hold_kind is None


def test_nine_dependents_is_not_held():
    d = evaluate(make_candidate(is_leaf=False, dependents=9), CTX)
    assert d.rule_id == "R12"


def test_soak():
    def at(days):
        return make_ctx(first_seen=lambda n, c: NOW - timedelta(days=days))

    c = make_candidate()
    assert evaluate(c, at(2)).rule_id == "R10"
    assert evaluate(c, at(2)).verdict is Verdict.DEFER
    assert evaluate(c, at(3)).verdict is Verdict.ELIGIBLE
    never = make_ctx(first_seen=lambda n, c: None)
    assert evaluate(c, never).verdict is Verdict.DEFER


def test_trusted_tap_passes_r06():
    c = make_candidate(tap="jandedobbeleer/oh-my-posh")
    assert evaluate(c, make_ctx()).rule_id == "R06"
    ctx = make_ctx(trusted_taps=frozenset({"jandedobbeleer/oh-my-posh"}))
    assert evaluate(c, ctx).verdict is Verdict.ELIGIBLE


def test_unchanged_caveat_hash_is_eligible_but_missing_hash_is_grey():
    c = make_candidate(caveats="Be careful")
    stored = make_ctx(caveat_hash=lambda n: caveat_hash("Be careful"))
    assert evaluate(c, stored).verdict is Verdict.ELIGIBLE
    assert evaluate(c, make_ctx()).verdict is Verdict.GREY
    other = make_ctx(caveat_hash=lambda n: caveat_hash("old"))
    assert evaluate(c, other).rule_id == "R11"


@pytest.mark.parametrize("text", [None, "", "  \n "])
def test_blank_caveats_are_no_signal(text):
    assert evaluate(make_candidate(caveats=text), make_ctx()).verdict is (
        Verdict.ELIGIBLE
    )


def test_pinned_wins_over_tap_and_live():
    c = make_candidate("vectordotdev/brew/vector", pinned=True, tap="vectordotdev/brew")
    assert evaluate(c, CTX).rule_id == "R02"


def test_evaluate_all_preserves_order():
    a, b = make_candidate("a"), make_candidate("b", pinned=True)
    ds = evaluate_all(make_snapshot(a, b), make_ctx())
    assert [(d.full_name, d.rule_id) for d in ds] == [("a", "R12"), ("b", "R02")]


@pytest.mark.parametrize("verdict", [v for v in Verdict if v is not Verdict.GREY])
@pytest.mark.parametrize("kind", ["upgrade", "hold", "transient"])
def test_no_model_outcome_changes_non_grey(verdict, kind):
    d = Decision("p", verdict, "Rxx", "r")
    assert apply_outcome(d, TriageOutcome(kind, "x", "claude")) == d


def test_real_fixtures():
    snap = build_snapshot(**fixture_inputs())
    ctx = make_ctx(live=frozenset({"vectordotdev/brew/vector", "rabbitmq"}))
    got = {d.full_name: (d.verdict, d.rule_id) for d in evaluate_all(snap, ctx)}
    assert got["redis"] == (Verdict.SKIP, "R02")
    assert got["jandedobbeleer/oh-my-posh/oh-my-posh"] == (Verdict.HOLD, "R06")
    assert got["vectordotdev/brew/vector"] == (Verdict.HOLD, "R06")
    assert got["rabbitmq"] == (Verdict.HOLD, "R08")
    assert got["antigravity-cli"] == (Verdict.REPORT, "R03")
