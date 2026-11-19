import pytest

from builders import make_candidate, make_snapshot
from morning_brew.brew.snapshot import build_snapshot
from morning_brew.components import plan_components, require_valid, validate_name
from morning_brew.domain import Decision, InvalidPackageName, Kind, Verdict
from test_snapshot import fixture_inputs


def _d(name: str, verdict: Verdict, reason: str = "") -> Decision:
    return Decision(name, verdict, "T", reason or f"{verdict.value}: {name}")


def elig(n: str) -> Decision:
    return _d(n, Verdict.ELIGIBLE)


def hold(n: str) -> Decision:
    return _d(n, Verdict.HOLD, f"held: {n} (R09 major)")


@pytest.fixture
def snap_aws():
    s = build_snapshot(**fixture_inputs())
    return s


@pytest.mark.parametrize(
    "n", ["postgresql@16", "vectordotdev/brew/vector", "python@3.14", "a.b_c+d-e"]
)
def test_valid(n):
    assert validate_name(n)


@pytest.mark.parametrize(
    "n",
    ["foo; rm -rf ~", "-x", "a/../b", "", "a b", "a\n", "café", "١٢"],
)
def test_invalid(n):
    assert not validate_name(n)


def test_require_valid_raises_on_first_bad_name():
    require_valid(["ok", "also-ok"])
    with pytest.raises(InvalidPackageName):
        require_valid(["ok", "--greedy"])


def test_aws_trio_is_one_component_and_hold_blocks_all(snap_aws):
    names = {c.full_name for c in snap_aws.candidates}
    assert {"aws-c-auth", "aws-c-s3", "awscli"} <= names
    decisions = [hold(n) if n == "awscli" else elig(n) for n in sorted(names)]
    plans = plan_components(snap_aws, decisions)
    aws = [p for p in plans if "awscli" in p.members]
    assert len(aws) == 1
    assert {"aws-c-auth", "aws-c-s3", "awscli"} <= set(aws[0].members)
    assert aws[0].members == tuple(sorted(aws[0].members))
    assert aws[0].status is Verdict.HOLD and aws[0].blocked_by == "awscli"
    assert "awscli" in aws[0].reason and "R09" in aws[0].reason
    assert [p.members[0] for p in plans] == sorted(p.members[0] for p in plans)


def test_all_eligible_component_is_eligible():
    snap = make_snapshot(make_candidate("a", runtime_deps=("b",)), make_candidate("b"))
    (p,) = plan_components(snap, [elig("a"), elig("b")])
    assert p.members == ("a", "b")
    assert p.status is Verdict.ELIGIBLE and p.blocked_by is None


def test_report_member_defers_component():
    snap = make_snapshot(make_candidate("a", runtime_deps=("b",)), make_candidate("b"))
    (p,) = plan_components(snap, [elig("a"), _d("b", Verdict.REPORT)])
    assert p.status is Verdict.DEFER and p.blocked_by == "b"


def test_singleton_report_stays_report():
    snap = make_snapshot(make_candidate("a"))
    (p,) = plan_components(snap, [_d("a", Verdict.REPORT)])
    assert p.status is Verdict.REPORT and p.members == ("a",)


def test_precedence_hold_skip_defer_grey_eligible():
    snap = make_snapshot(
        make_candidate("a", runtime_deps=("b", "c", "d", "e")),
        make_candidate("b"),
        make_candidate("c"),
        make_candidate("d"),
        make_candidate("e"),
    )
    base = {
        "a": elig("a"),
        "b": _d("b", Verdict.GREY),
        "c": _d("c", Verdict.DEFER),
        "d": _d("d", Verdict.SKIP),
        "e": hold("e"),
    }
    expected = [
        (Verdict.HOLD, "e"),
        (Verdict.SKIP, "d"),
        (Verdict.DEFER, "c"),
        (Verdict.GREY, None),
    ]
    for status, blocker in expected:
        (p,) = plan_components(snap, list(base.values()))
        assert (p.status, p.blocked_by) == (status, blocker)
        base[blocker or "b"] = elig(blocker or "b")
        if status is Verdict.GREY:
            break


def test_blocker_is_first_sorted_with_winning_status():
    snap = make_snapshot(
        make_candidate("z", runtime_deps=("m", "b")),
        make_candidate("m"),
        make_candidate("b"),
    )
    (p,) = plan_components(snap, [hold("z"), hold("m"), elig("b")])
    assert p.blocked_by == "m"


def test_cycle_and_singleton_ok():
    snap = make_snapshot(
        make_candidate("a", runtime_deps=("b",)),
        make_candidate("b", runtime_deps=("a",)),
        make_candidate("solo"),
    )
    plans = plan_components(snap, [elig("a"), elig("b"), elig("solo")])
    assert [p.members for p in plans] == [("a", "b"), ("solo",)]
    assert all(p.status is Verdict.ELIGIBLE for p in plans)


def test_non_outdated_dep_creates_no_edge():
    # a and c both depend on installed-but-not-outdated "libx"
    snap = make_snapshot(
        make_candidate("a", runtime_deps=("libx",)),
        make_candidate("c", runtime_deps=("libx",)),
    )
    plans = plan_components(snap, [elig("a"), elig("c")])
    assert [p.members for p in plans] == [("a",), ("c",)]


def test_casks_are_singletons_and_never_join():
    snap = make_snapshot(
        make_candidate("a", runtime_deps=("k",)),
        make_candidate("k", kind=Kind.CASK),
    )
    plans = plan_components(snap, [elig("a"), _d("k", Verdict.REPORT)])
    assert [(p.members, p.status) for p in plans] == [
        (("a",), Verdict.ELIGIBLE),
        (("k",), Verdict.REPORT),
    ]


def test_missing_decision_fails_closed():
    snap = make_snapshot(make_candidate("a"))
    (p,) = plan_components(snap, [])
    assert p.status is Verdict.HOLD and p.blocked_by == "a"
