from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import timedelta

import pytest

from builders import NOW, make_candidate, make_snapshot
from fakes import FakeAudit, FakeBrew, FakeHolds, FakeLive
from morning_brew.components import plan_components
from morning_brew.domain import Hold, HoldKind, UpgradeResult
from morning_brew.review import ReviewResult, review


@dataclass
class RecHolds(FakeHolds):
    skipped: list[tuple[str, str]] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    def release(self, name: str) -> None:
        self.log.append(f"release:{name}")
        super().release(name)

    def mark_skipped(self, name: str, version: str) -> None:
        self.skipped.append((name, version))
        old = self.holds[name]
        self.holds[name] = Hold(
            name=old.name,
            kind=old.kind,
            held_at_version=old.held_at_version,
            reason=old.reason,
            first_held=old.first_held,
            skipped_for_version=version,
        )


def hold(name: str, version: str = "1.0.1", **kw: object) -> Hold:
    base = {
        "name": name,
        "kind": HoldKind.DECIDED,
        "held_at_version": version,
        "reason": "major bump",
        "first_held": NOW - timedelta(days=5),
    }
    base.update(kw)
    return Hold(**base)  # type: ignore[arg-type]


def script(answers: list[str]) -> Callable[[str], str]:
    it: Iterator[str] = iter(answers)

    def ask(prompt: str) -> str:
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    return ask


def snap() -> object:
    return make_snapshot(
        make_candidate("awscli", runtime_deps=("aws-c-auth",)),
        make_candidate("aws-c-auth", runtime_deps=("aws-c-s3",)),
        make_candidate("aws-c-s3"),
        make_candidate("grype", current="0.122.0"),
        make_candidate("other"),
    )


class Env:
    def __init__(self, holds: list[Hold], live: frozenset[str] = frozenset()) -> None:
        self.calls: list[str] = []
        self.holds = RecHolds({h.name: h for h in holds})
        self.brew = FakeBrew(self.calls, [snap()])  # type: ignore[arg-type]
        self.live = FakeLive(live)
        self.audit = FakeAudit(self.calls)
        self.out: list[str] = []

    def run(
        self, answers: list[str], self_deps: frozenset[str] = frozenset()
    ) -> ReviewResult:
        return review(
            self.holds,
            self.brew,
            lambda s, d: plan_components(s, d),
            self.live,
            self.audit,
            ask=script(answers),
            say=self.out.append,
            now=NOW,
            run_id="r",
            self_deps=self_deps,
        )


def two() -> Env:
    return Env([hold("awscli"), hold("grype", "0.122.0")])


def test_approve_one_skip_one() -> None:
    env = two()
    r = env.run(["a", "s"])
    assert env.brew.upgrades == [("aws-c-auth", "aws-c-s3", "awscli")]
    assert r.skipped == ("grype",)
    assert r.approved == ("awscli",)
    assert env.holds.get("grype").skipped_for_version == "0.122.0"  # type: ignore[union-attr]
    assert env.holds.get("awscli") is None


def test_live_process_member_needs_explicit_yes() -> None:
    env = Env([hold("awscli")], live=frozenset({"aws-c-s3"}))
    r = env.run(["a", "no"])
    assert env.brew.upgrades == [] and env.holds.get("awscli") is not None
    assert r.approved == ()
    env = Env([hold("awscli")], live=frozenset({"aws-c-s3"}))
    env.run(["a", "yes"])
    assert len(env.brew.upgrades) == 1


def test_reasoning_is_labeled_and_stripped() -> None:
    env = Env([hold("grype", model_reasoning="bad \x1b[31mred\x1b[0m " + "x" * 900)])
    env.run(["d"])
    labeled = [line for line in env.out if "(derived from untrusted text)" in line]
    assert labeled and not any("\x1b" in line for line in env.out)
    assert all(len(line) < 700 for line in labeled)
    assert "x" * 501 not in "".join(env.out)


def test_display_has_package_version_reason_age() -> None:
    env = Env([hold("grype", "0.122.0")])
    env.run(["d"])
    text = "\n".join(env.out)
    assert "grype" in text and "0.122.0" in text and "major bump" in text
    assert "5 days" in text


def test_review_never_pins() -> None:
    env = two()
    env.run(["a", "s"])
    assert not any("pin" in c for c in env.calls)


def test_only_decided_holds_listed() -> None:
    env = Env(
        [
            hold("awscli"),
            hold("grype", kind=HoldKind.TRANSIENT),
        ]
    )
    prompts: list[str] = []

    def ask(p: str) -> str:
        prompts.append(p)
        return "d"

    r = review(
        env.holds, env.brew, plan_components, env.live, env.audit,
        ask=ask, say=env.out.append, now=NOW, run_id="r",
    )  # fmt: skip
    assert r.deferred == ("awscli",) and len(prompts) == 1
    assert not any("grype" in line for line in env.out)


def test_audit_records_review_actions() -> None:
    env = two()
    env.run(["a", "s"])
    evs = [e for e in env.audit.events if e.stage == "review"]
    assert [(e.package, e.verdict, e.rule_id) for e in evs] == [
        ("awscli", "approved", None),
        ("grype", "skipped", None),
    ]
    env = Env([hold("grype")])
    env.run(["d"])
    assert env.audit.events[-1].verdict == "deferred"


def test_no_longer_outdated_releases_without_upgrade() -> None:
    env = Env([hold("gone")])
    r = env.run(["a"])
    assert env.brew.upgrades == [] and env.holds.get("gone") is None
    assert any("no longer outdated" in line for line in env.out)
    assert r.approved == ()


def test_busy_keeps_hold() -> None:
    env = Env([hold("awscli")])
    env.brew.busy_on = frozenset({"awscli"})
    r = env.run(["a"])
    assert env.holds.get("awscli") is not None
    assert any("brew busy, try later" in line for line in env.out)
    assert r.approved == ()


def test_failed_upgrade_keeps_hold_and_shows_redacted_tail() -> None:
    env = Env([hold("awscli")])
    env.brew.upgrade_results = {"awscli": UpgradeResult(False, "boom \x1b[31m!")}
    env.run(["a"])
    assert env.holds.get("awscli") is not None
    assert any("boom" in line for line in env.out)
    assert not any("\x1b" in line for line in env.out)


def test_self_dependency_shows_command_not_upgrade() -> None:
    env = Env([hold("awscli")])
    r = env.run(["a"], self_deps=frozenset({"aws-c-s3"}))
    assert env.brew.upgrades == [] and env.holds.get("awscli") is not None
    assert any("brew upgrade -- aws-c-auth aws-c-s3 awscli" in line for line in env.out)
    assert r.approved == ()


def test_unknown_answers_reprompt_then_defer() -> None:
    env = Env([hold("awscli")])
    r = env.run(["x", "?", "zz"])
    assert r.deferred == ("awscli",) and env.brew.upgrades == []


def test_eof_stops_cleanly() -> None:
    env = two()
    r = env.run(["s"])  # second prompt hits EOF
    assert r.skipped == ("awscli",) or r.skipped == ("grype",)
    assert len(r.skipped) == 1 and r.deferred == () and r.approved == ()


def test_skipped_holds_not_relisted() -> None:
    env = Env([hold("grype", "0.122.0", skipped_for_version="0.122.0")])
    r = env.run([])
    assert r == ReviewResult((), (), ())


@pytest.mark.parametrize("ans", ["A", " a "])
def test_answers_normalised(ans: str) -> None:
    env = Env([hold("grype")])
    assert env.run([ans]).approved == ("grype",)
