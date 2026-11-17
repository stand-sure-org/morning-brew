import json
import logging
import os
import stat
from datetime import UTC, datetime, timedelta, timezone

import pytest

from morning_brew.domain import AuditEvent
from morning_brew.sanitize import redact, tail_redacted
from morning_brew.store.audit import JsonlAudit

# Secret-shaped fixtures are assembled at runtime so no literal trips gitleaks.
TAIL = "Q7xZ" + "9" * 12
EXPECTED_KEYS = {
    "run_id",
    "ts",
    "brew_version",
    "stage",
    "package",
    "rule_id",
    "verdict",
    "action_result",
    "models",
    "booleans",
    "reasoning",
    "component",
    "hold_kind",
}
T0 = datetime(2026, 11, 17, 7, 0, tzinfo=UTC)


def ev(package: str, **kw) -> AuditEvent:
    base = {
        "run_id": "r1",
        "ts": T0,
        "brew_version": "7.0.14",
        "stage": "decision",
        "package": package,
    }
    base.update(kw)
    return AuditEvent(**base)


@pytest.mark.parametrize(
    "prefix", ["Bearer ", "sk-" + "ant-api03-", "gh" + "p_", "token=", "password="]
)
def test_redacts(prefix):
    out = redact(prefix + TAIL)
    assert TAIL not in out and "***REDACTED***" in out


def test_redacts_url_credentials_and_github_pat():
    assert TAIL not in redact("https://user:" + TAIL + "@host/x")
    assert TAIL not in redact("github" + "_pat_" + TAIL)
    assert TAIL not in redact("api key " + TAIL)
    assert TAIL not in redact("API_KEY=" + TAIL)


def test_tail_redacted_keeps_last_chars_after_redaction():
    text = "A" * 50 + " password=" + TAIL + " END"
    out = tail_redacted(text, limit=10)
    assert len(out) == 10 and out.endswith(" END")
    assert TAIL not in tail_redacted(text)
    assert tail_redacted("short") == "short"


def test_append_only_and_schema(tmp_path):
    a = JsonlAudit(tmp_path / "audit.jsonl")
    a.record(ev("a"))
    first = (tmp_path / "audit.jsonl").read_text()
    a.record(ev("b"))
    assert (tmp_path / "audit.jsonl").read_text().startswith(first)
    assert set(json.loads(first.splitlines()[0])) == EXPECTED_KEYS


def test_roundtrip_fields(tmp_path):
    a = JsonlAudit(tmp_path / "audit.jsonl")
    ts = datetime(2026, 11, 17, 7, 5, tzinfo=timezone(timedelta(hours=-5)))
    e = ev(
        "pkg",
        ts=ts,
        rule_id="R1",
        verdict="hold",
        models=(("qwen3:4b", "approve"), ("claude", None)),
        booleans={"a": True, "b": False},
        reasoning="because é",
        component="a,b",
        hold_kind="transient",
    )
    a.record(e)
    raw = json.loads((tmp_path / "audit.jsonl").read_text())
    assert raw["models"] == [["qwen3:4b", "approve"], ["claude", None]]
    assert raw["booleans"] == {"a": True, "b": False}
    [got] = a.events_for("pkg")
    assert got == e and got.ts.utcoffset() == ts.utcoffset()


def test_booleans_none_roundtrips(tmp_path):
    a = JsonlAudit(tmp_path / "audit.jsonl")
    a.record(ev("p"))
    assert json.loads((tmp_path / "audit.jsonl").read_text())["booleans"] is None
    assert a.events_for("p")[0].booleans is None


def test_reasoning_and_stderr_are_redacted(tmp_path):
    a = JsonlAudit(tmp_path / "audit.jsonl")
    a.record(ev("p", reasoning="Bearer " + TAIL, action_result="failed: token=" + TAIL))
    text = (tmp_path / "audit.jsonl").read_text()
    assert TAIL not in text and "***REDACTED***" in text
    [got] = a.events_for("p")
    assert TAIL not in (got.reasoning or "") and TAIL not in (got.action_result or "")


def test_truncated_last_line_is_skipped_on_read(tmp_path, caplog):
    path = tmp_path / "audit.jsonl"
    a = JsonlAudit(path)
    a.record(ev("p", reasoning="one"))
    with open(path, "a") as fh:
        fh.write('{"run_id": "r1", "ts": "2026-11-17T07:0')
    with caplog.at_level(logging.WARNING):
        got = a.events_for("p")
    assert [e.reasoning for e in got] == ["one"]
    assert "corrupt" in caplog.text


def test_unknown_keys_tolerated_and_missing_required_skipped(tmp_path):
    path = tmp_path / "audit.jsonl"
    a = JsonlAudit(path)
    a.record(ev("p", reasoning="ok"))
    good = json.loads(path.read_text())
    with open(path, "a") as fh:
        fh.write(json.dumps({**good, "future": 1, "reasoning": "extra"}) + "\n")
        bad = {k: v for k, v in good.items() if k != "run_id"}
        fh.write(json.dumps(bad) + "\n")
    assert [e.reasoning for e in a.events_for("p")] == ["ok", "extra"]


def test_events_for_filters_by_package_and_missing_file(tmp_path):
    a = JsonlAudit(tmp_path / "nope" / "audit.jsonl")
    assert a.events_for("p") == []
    a.record(ev("p"))
    a.record(ev("q"))
    assert [e.package for e in a.events_for("q")] == ["q"]


def test_events_for_preserves_append_order(tmp_path):
    a = JsonlAudit(tmp_path / "audit.jsonl")
    for r in ("z", "a", "m", "b"):
        a.record(ev("p", reasoning=r))
    assert [e.reasoning for e in a.events_for("p")] == ["z", "a", "m", "b"]


def test_file_mode_0600(tmp_path):
    path = tmp_path / "sub" / "audit.jsonl"
    JsonlAudit(path).record(ev("p"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_partial_last_line_does_not_swallow_next_event(tmp_path, caplog):
    path = tmp_path / "audit.jsonl"
    path.write_text('{"run_id": "r1", "ts": "2026-11-17T07:0')
    a = JsonlAudit(path)
    a.record(ev("b"))
    with caplog.at_level(logging.WARNING):
        got = a.events_for("b")
    assert [e.package for e in got] == ["b"]
    assert "corrupt" in caplog.text


@pytest.mark.parametrize(
    "text",
    [
        "password: " + TAIL,
        "token: " + TAIL,
        '{"password": "' + TAIL + '"}',
        '{"token":"' + TAIL + '"}',
        "API_KEY: " + TAIL,
        "api key: " + TAIL,
        "secret: " + TAIL,
        "api_key=" + TAIL,
    ],
)
def test_redacts_colon_and_json_forms(text):
    out = redact(text)
    assert TAIL not in out and "***REDACTED***" in out


@pytest.mark.parametrize(
    "text",
    [
        "the API key rotation is due",
        "invalid token in cache",
        "api key is rotated weekly",
        "password policy updated",
    ],
)
def test_prose_untouched(text):
    assert redact(text) == text


def test_truncated_multibyte_tail_does_not_crash_read(tmp_path, caplog):
    path = tmp_path / "audit.jsonl"
    a = JsonlAudit(path)
    a.record(ev("p", reasoning="intact"))
    with open(path, "ab") as fh:
        fh.write(b'{"run_id": "r1", "reasoning": "caf\xc3')
    with caplog.at_level(logging.WARNING):
        got = a.events_for("p")
    assert [e.reasoning for e in got] == ["intact"]
    assert "corrupt" in caplog.text


def test_loose_existing_file_is_tightened(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text("")
    path.chmod(0o644)
    JsonlAudit(path).record(ev("p"))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_new_file_never_looser_than_0600(tmp_path):
    old = os.umask(0)
    try:
        path = tmp_path / "audit.jsonl"
        JsonlAudit(path).record(ev("p"))
    finally:
        os.umask(old)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
