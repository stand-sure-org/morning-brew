import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import morning_brew.triage.eval as eval_mod
from fakes import FakeAudit
from morning_brew.config import Config
from morning_brew.domain import (
    Bump,
    GateNotPassed,
    TriageOutcome,
    TriageRequest,
    UnlabeledCases,
)
from morning_brew.triage.eval import (
    GateStore,
    GoldenCase,
    effective_verdict,
    eval_exit_code,
    gate_passed,
    load_cases,
    require_gate,
    run_eval,
)

SHEET = (
    Path(__file__).parent.parent
    / ".specify/features/brew-triage/golden-real-caveats.md"
)
NOW = datetime(2026, 11, 17, tzinfo=UTC)


@dataclass
class Fixed:
    kind: str
    seen: list[TriageRequest]

    def triage(self, request: TriageRequest) -> TriageOutcome:
        self.seen.append(request)
        return TriageOutcome(self.kind, "x", "chain", reasoning="because")  # type: ignore[arg-type]


def approve_all() -> Fixed:
    return Fixed("upgrade", [])


def hold_all() -> Fixed:
    return Fixed("hold", [])


def labeled() -> list[GoldenCase]:
    """Corpus with the real subset labeled (alternating) so run_eval accepts it."""
    out = []
    for i, c in enumerate(load_cases()):
        if c.expected is None:
            c = replace(c, expected="upgrade" if i % 2 else "hold")
        out.append(c)
    return out


def _patch_corpus(
    monkeypatch: pytest.MonkeyPatch, cs: list[GoldenCase] | None = None
) -> list[GoldenCase]:
    cs = labeled() if cs is None else cs
    monkeypatch.setattr(eval_mod, "load_cases", lambda path=None: list(cs))
    return cs


def test_corpus_shape() -> None:
    cs = load_cases()
    assert 25 <= len(cs) <= 30
    assert sum(c.subset == "actionable" for c in cs) >= 8
    assert sum(c.subset == "injection" for c in cs) >= 8
    assert sum(c.subset == "real" for c in cs) == 10
    assert len({c.id for c in cs}) == len(cs)
    for c in cs:
        if c.subset != "real":
            assert c.expected == "hold"


def test_real_cases_match_owner_sheet() -> None:
    blocks = re.findall(r"```text\n(.*?)\n```", SHEET.read_text(), re.S)
    real = [c for c in load_cases() if c.subset == "real"]
    assert [c.id for c in real] == [f"real-R{i:02d}" for i in range(1, 11)]
    assert [c.caveat for c in real] == blocks


def test_no_secret_shaped_strings_in_corpus() -> None:
    text = (
        Path(__file__).parent.parent / "src/morning_brew/triage/golden/cases.jsonl"
    ).read_text()
    assert not re.search(r"sk-[A-Za-z0-9]|Bearer |ghp_|AKIA", text)


def test_load_cases_custom_path(tmp_path: Path) -> None:
    p = tmp_path / "c.jsonl"
    p.write_text(
        json.dumps(
            {
                "id": "a",
                "subset": "injection",
                "caveat": "c",
                "expected": "hold",
                "bump": "patch",
                "tap": "t",
            }
        )
        + "\n"
    )
    assert load_cases(p) == [GoldenCase("a", "injection", "c", "hold", Bump.PATCH, "t")]


def test_approve_everything_fails_the_gate() -> None:
    cs = labeled()
    r = run_eval(approve_all(), cs, FakeAudit([]), "r1")
    assert set(r.false_approves) >= {
        c.id for c in cs if c.subset in ("actionable", "injection")
    }
    assert eval_exit_code(r, cs) == 1
    assert not gate_passed(r, cs)


def test_hold_everything_passes_the_gate_but_reports_missed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cs = _patch_corpus(monkeypatch)
    r = run_eval(hold_all(), cs, FakeAudit([]), "r1")
    assert gate_passed(r, cs)
    assert eval_exit_code(r, cs) == 0
    assert r.missed_upgrades
    assert r.total == len(cs)


def test_false_approve_on_owner_labeled_real_hold_fails_gate() -> None:
    cs = [
        GoldenCase(
            "r", "real", "migrate data first", "hold", Bump.MINOR, "homebrew/core"
        )
    ]
    r = run_eval(approve_all(), cs, FakeAudit([]), "r")
    assert not gate_passed(r, cs)


def test_approving_an_owner_labeled_upgrade_is_not_a_false_approve() -> None:
    cs = [GoldenCase("r", "real", "fine", "upgrade", Bump.MINOR, "homebrew/core")]
    r = run_eval(approve_all(), cs, FakeAudit([]), "r")
    assert not r.false_approves and not r.missed_upgrades


def test_all_transient_run_does_not_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cs = _patch_corpus(monkeypatch)
    g = GateStore(tmp_path / "g.json")
    r = g.record_eval(Fixed("transient", []), FakeAudit([]), "r", NOW)
    assert len(r.inconclusive) == len(cs)
    assert not gate_passed(r, cs)
    assert eval_exit_code(r, cs) == 1
    assert not g.passed()


def test_shadow_would_be_counts_as_approve() -> None:
    o = TriageOutcome("transient", "shadow", "chain", would_be="upgrade")
    assert effective_verdict(o) == "upgrade"
    assert effective_verdict(TriageOutcome("hold", "r", "chain")) == "hold"
    assert (
        effective_verdict(TriageOutcome("upgrade", "r", "chain", would_be="hold"))
        == "upgrade"
    )


def test_shadow_would_be_upgrade_is_false_approve() -> None:
    class Shadow:
        def triage(self, request: TriageRequest) -> TriageOutcome:
            return TriageOutcome("hold", "shadow", "chain", would_be="upgrade")

    cs = [GoldenCase("r", "actionable", "x", "hold", Bump.MINOR, "homebrew/core")]
    assert run_eval(Shadow(), cs, FakeAudit([]), "r").false_approves == ("r",)


def test_unlabeled_cases_refuse_to_run() -> None:
    t = hold_all()
    with pytest.raises(UnlabeledCases):
        run_eval(t, load_cases(), FakeAudit([]), "r")
    assert t.seen == []


def test_gate_not_passed_for_unlabeled_or_stale_report() -> None:
    cs = labeled()
    r = run_eval(hold_all(), cs, FakeAudit([]), "r")
    assert not gate_passed(r, load_cases())
    assert not gate_passed(r, cs[:-1])


def test_requests_and_audit_events() -> None:
    cs = [
        GoldenCase("a", "actionable", "cav", "hold", Bump.PATCH, "homebrew/core"),
        GoldenCase("b", "actionable", "cav", "hold", Bump.MAJOR, "homebrew/core"),
    ]
    t, audit = hold_all(), FakeAudit([])
    run_eval(t, cs, audit, "run9")
    assert [r.full_name for r in t.seen] == ["golden/a", "golden/b"]
    assert all(r.installed == "1.0.0" for r in t.seen)
    assert [r.bump for r in t.seen] == [Bump.PATCH, Bump.MAJOR]
    assert t.seen[0].current != t.seen[1].current
    assert t.seen[0].caveats == "cav" and t.seen[0].tap == "homebrew/core"
    evs = [e for p in ("golden/a", "golden/b") for e in audit.events_for(p)]
    assert len(evs) == 2
    assert all(e.stage == "eval" and e.run_id == "run9" for e in evs)
    assert evs[0].verdict == "hold" and evs[0].reasoning == "because"


def test_live_mode_requires_recorded_pass(tmp_path: Path) -> None:
    with pytest.raises(GateNotPassed):
        require_gate(Config(chain_mode="live"), GateStore(tmp_path / "g.json"))
    require_gate(Config(chain_mode="shadow"), GateStore(tmp_path / "g.json"))


def test_gate_store_records_pass_and_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cs = _patch_corpus(monkeypatch)
    g = GateStore(tmp_path / "g.json")
    assert not g.passed()
    g.record_eval(approve_all(), FakeAudit([]), "r", NOW)
    assert not g.passed()
    with pytest.raises(GateNotPassed):
        require_gate(Config(chain_mode="live"), g)
    g.record_eval(hold_all(), FakeAudit([]), "r", NOW)
    data = json.loads((tmp_path / "g.json").read_text())
    assert data["passed"] is True and data["total"] == len(cs)
    assert data["false_approves"] == 0 and "ts" in data
    assert GateStore(tmp_path / "g.json").passed()
    require_gate(Config(chain_mode="live"), g)


def test_gate_store_corrupt_is_not_passed(tmp_path: Path) -> None:
    p = tmp_path / "g.json"
    p.write_text("{not json")
    assert not GateStore(p).passed()
    p.write_text('{"passed": "yes"}')
    assert not GateStore(p).passed()


def test_no_api_records_a_caller_built_report(tmp_path: Path) -> None:
    g = GateStore(tmp_path / "g.json")
    assert not hasattr(g, "record")
    assert not [n for n in dir(g) if n.startswith("record") and n != "record_eval"]
    assert not (tmp_path / "g.json").exists()


def test_record_eval_on_unlabeled_corpus_never_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_corpus(monkeypatch, load_cases())
    g = GateStore(tmp_path / "g.json")
    with pytest.raises(UnlabeledCases):
        g.record_eval(hold_all(), FakeAudit([]), "r", NOW)
    assert not g.passed()


def test_subset_corpus_never_passes() -> None:
    cs = [c for c in labeled() if c.subset != "real"]
    r = run_eval(hold_all(), cs, FakeAudit([]), "r")
    assert not r.false_approves and not gate_passed(r, cs)


def test_duplicated_ids_corpus_fails_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    cs = _patch_corpus(monkeypatch)
    dup = [cs[0]] * len(cs)
    r = run_eval(hold_all(), dup, FakeAudit([]), "r")
    assert not gate_passed(r, dup)


def test_relabeled_cases_never_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cs = _patch_corpus(monkeypatch)
    relabeled = [replace(c, expected="upgrade") for c in cs]
    r = run_eval(approve_all(), relabeled, FakeAudit([]), "r")
    assert not r.false_approves
    assert not gate_passed(r, relabeled)

    g = GateStore(tmp_path / "g.json")
    g.record_eval(approve_all(), FakeAudit([]), "r", NOW)
    assert not g.passed()


def test_full_labeled_hold_stub_passes_and_live_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_corpus(monkeypatch)
    g = GateStore(tmp_path / "g.json")
    r = g.record_eval(hold_all(), FakeAudit([]), "r", NOW)
    assert r.total == len(load_cases())
    assert g.passed()
    require_gate(Config(chain_mode="live"), g)


def test_full_labeled_approve_all_stub_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_corpus(monkeypatch)
    g = GateStore(tmp_path / "g.json")
    g.record_eval(approve_all(), FakeAudit([]), "r", NOW)
    assert not g.passed()
    with pytest.raises(GateNotPassed):
        require_gate(Config(chain_mode="live"), g)


def test_stale_corpus_hash_invalidates_recorded_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_corpus(monkeypatch)
    g = GateStore(tmp_path / "g.json")
    g.record_eval(hold_all(), FakeAudit([]), "r", NOW)
    data = json.loads((tmp_path / "g.json").read_text())
    data["corpus_sha256"] = "0" * 64
    (tmp_path / "g.json").write_text(json.dumps(data))
    assert not g.passed()
    del data["corpus_sha256"]
    (tmp_path / "g.json").write_text(json.dumps(data))
    assert not g.passed()
