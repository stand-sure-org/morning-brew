import logging
import os
import stat
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from morning_brew.domain import Hold, HoldKind, StateUnwritable
from morning_brew.store.holds import FileHoldStore, is_suppressed, needs_retriage

T1 = datetime(2026, 11, 1, 7, 0, tzinfo=UTC)


def hold(name: str, **kw) -> Hold:
    base = {
        "name": name,
        "kind": HoldKind.DECIDED,
        "held_at_version": "1.0",
        "reason": "rule said no",
        "first_held": T1,
    }
    base.update(kw)
    return Hold(**base)


def mode(p: Path) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


def test_modes(tmp_path):
    s = FileHoldStore(tmp_path)
    s.put(hold("a"))
    files = list((tmp_path / "holds").glob("*.json"))
    assert mode(tmp_path / "holds") == 0o700 and mode(files[0]) == 0o600


def test_existing_dir_mode_tightened(tmp_path):
    d = tmp_path / "holds"
    d.mkdir(mode=0o755)
    d.chmod(0o755)
    FileHoldStore(tmp_path).put(hold("a"))
    assert mode(d) == 0o700
    d.chmod(0o755)
    FileHoldStore(tmp_path).ensure_writable()
    assert mode(d) == 0o700


def test_tap_name_round_trips(tmp_path):
    s = FileHoldStore(tmp_path)
    s.put(hold("vectordotdev/brew/vector"))
    assert s.get("vectordotdev/brew/vector").name == "vectordotdev/brew/vector"
    assert (tmp_path / "holds" / "vectordotdev%2Fbrew%2Fvector.json").exists()


def test_all_fields_round_trip(tmp_path):
    h = hold(
        "a",
        kind=HoldKind.TRANSIENT,
        blocked_by="brew-busy",
        model_reasoning="because",
        skipped_for_version="1.0",
    )
    s = FileHoldStore(tmp_path)
    s.put(h)
    assert s.get("a") == h and s.get("a").first_held.utcoffset() is not None


def test_retriage_and_skip_apply_per_version():
    h = hold("a", held_at_version="1.0", skipped_for_version="1.0")
    assert is_suppressed(h, "1.0") and not is_suppressed(h, "1.1")
    assert needs_retriage(h, "1.1") and not needs_retriage(h, "1.0")
    assert not is_suppressed(hold("a"), "1.0")


def test_crash_mid_write_keeps_old(tmp_path, monkeypatch):
    s = FileHoldStore(tmp_path)
    old = hold("a")
    s.put(old)

    def boom(*a, **k):
        raise OSError("crash")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        s.put(replace(old, reason="new"))
    monkeypatch.undo()
    assert s.get("a") == old
    assert not list((tmp_path / "holds").glob("*.tmp"))


def test_corrupt_hold_file_is_absent(tmp_path):
    (tmp_path / "holds").mkdir()
    (tmp_path / "holds" / "a.json").write_text("{")
    assert FileHoldStore(tmp_path).get("a") is None


def test_wrong_shape_is_absent(tmp_path):
    (tmp_path / "holds").mkdir()
    (tmp_path / "holds" / "a.json").write_text('{"name": "a"}')
    assert FileHoldStore(tmp_path).get("a") is None


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_ensure_writable_raises(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    root.chmod(0o500)
    try:
        with pytest.raises(StateUnwritable):
            FileHoldStore(root).ensure_writable()
    finally:
        root.chmod(0o700)


def test_ensure_writable_creates_dir_and_leaves_no_probe(tmp_path):
    FileHoldStore(tmp_path).ensure_writable()
    assert mode(tmp_path / "holds") == 0o700
    assert list((tmp_path / "holds").iterdir()) == []


def test_ensure_writable_when_root_is_a_file(tmp_path):
    f = tmp_path / "state"
    f.write_text("x")
    with pytest.raises(StateUnwritable):
        FileHoldStore(f).ensure_writable()


def test_list_separates_kinds(tmp_path):
    s = FileHoldStore(tmp_path)
    s.put(hold("b", kind=HoldKind.TRANSIENT))
    s.put(hold("a"))
    s.put(hold("c/d"))
    (tmp_path / "holds" / "bad.json").write_text("{")
    got = s.list_all()
    assert [h.name for h in got] == ["a", "b", "c/d"]
    assert [h.kind for h in got] == [
        HoldKind.DECIDED,
        HoldKind.TRANSIENT,
        HoldKind.DECIDED,
    ]


def test_list_empty_when_no_dir(tmp_path):
    assert FileHoldStore(tmp_path).list_all() == []


def test_release(tmp_path):
    s = FileHoldStore(tmp_path)
    s.put(hold("a"))
    s.release("a")
    assert s.get("a") is None
    s.release("a")
    s.release("never")


def test_mark_skipped(tmp_path):
    s = FileHoldStore(tmp_path)
    s.put(hold("a"))
    s.mark_skipped("a", "1.0")
    assert s.get("a").skipped_for_version == "1.0"
    assert s.get("a").reason == "rule said no"


def test_mark_skipped_missing_is_noop_and_logs(tmp_path, caplog):
    s = FileHoldStore(tmp_path)
    with caplog.at_level(logging.INFO):
        s.mark_skipped("ghost", "1.0")
    assert s.get("ghost") is None and "ghost" in caplog.text
    assert not (tmp_path / "holds").exists() or not list(
        (tmp_path / "holds").glob("*.json")
    )


def test_symlinked_hold_is_skipped(tmp_path, caplog):
    s = FileHoldStore(tmp_path)
    s.put(hold("real"))
    d = tmp_path / "holds"
    target = tmp_path / "outside.json"
    (d / "real.json").rename(target)
    (d / "real.json").symlink_to(target)
    s.put(hold("other"))
    with caplog.at_level(logging.WARNING):
        assert s.get("real") is None
        assert [h.name for h in s.list_all()] == ["other"]
    assert "symlink" in caplog.text


def test_model_reasoning_is_redacted_before_storing(tmp_path):
    # [M1] the reasoning derives from untrusted text and is shown by review
    secret = "Q7xZ" + "9" * 12
    store = FileHoldStore(tmp_path)
    store.put(hold("grype", model_reasoning="it said token=" + secret + " ok"))
    raw = (tmp_path / "holds" / "grype.json").read_text()
    assert secret not in raw
    got = store.get("grype")
    assert got is not None and secret not in (got.model_reasoning or "")
    assert "***REDACTED***" in (got.model_reasoning or "")
