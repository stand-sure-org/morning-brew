import os
import stat
from datetime import UTC, datetime, timedelta

from morning_brew.caveats import caveat_hash
from morning_brew.store.seen import FileSeenStore, seed_caveat_hashes

T1 = datetime(2026, 11, 1, 7, 0, tzinfo=UTC)
T2 = T1 + timedelta(days=2)


def test_caveat_hash_normalization():
    assert caveat_hash(None) is None and caveat_hash("  \n") is None
    assert caveat_hash("") is None
    assert caveat_hash("a  b\n") == caveat_hash("a b") and caveat_hash(
        "a"
    ) != caveat_hash("b")


def test_first_seen_is_idempotent(tmp_path):
    s = FileSeenStore(tmp_path / "seen.json")
    s.record_first_seen("p", "1.0", T1)
    s.record_first_seen("p", "1.0", T2)
    assert s.first_seen("p", "1.0") == T1 and s.first_seen("p", "1.1") is None


def test_first_seen_persists_across_instances_as_aware(tmp_path):
    FileSeenStore(tmp_path / "seen.json").record_first_seen("p", "1.0", T1)
    got = FileSeenStore(tmp_path / "seen.json").first_seen("p", "1.0")
    assert got == T1 and got.tzinfo is not None


def test_due_for_cleanup_after_two_full_runs(tmp_path):
    s = FileSeenStore(tmp_path / "seen.json")
    s.record_upgrade(1, ["a"])
    s.record_upgrade(2, ["b"])
    assert s.due_for_cleanup(3, 2) == ["a"] and s.due_for_cleanup(2, 2) == []
    s.mark_cleaned(["a"])
    assert s.due_for_cleanup(3, 2) == []


def test_due_for_cleanup_sorted_deduped(tmp_path):
    s = FileSeenStore(tmp_path / "seen.json")
    s.record_upgrade(1, ["b", "a"])
    s.record_upgrade(2, ["a"])
    assert s.due_for_cleanup(4, 2) == ["a", "b"]


def test_caveat_hash_roundtrip(tmp_path):
    s = FileSeenStore(tmp_path / "seen.json")
    assert s.caveat_hash("p") is None
    s.record_caveat_hash("p", "abc")
    assert FileSeenStore(tmp_path / "seen.json").caveat_hash("p") == "abc"


def test_seed_counts_only_present_caveats(tmp_path):
    s = FileSeenStore(tmp_path / "seen.json")
    assert seed_caveat_hashes(s, {"a": "x", "b": None, "c": ""}) == 1
    assert s.caveat_hash("a") == caveat_hash("x") and s.caveat_hash("b") is None


def test_corrupt_file_reads_as_empty(tmp_path, caplog):
    (tmp_path / "seen.json").write_text("{")
    s = FileSeenStore(tmp_path / "seen.json")
    assert s.first_seen("p", "1") is None
    assert any(r.levelname == "WARNING" for r in caplog.records)
    s.record_first_seen("p", "1", T1)
    assert s.first_seen("p", "1") == T1


def test_wrong_shape_reads_as_empty(tmp_path):
    (tmp_path / "seen.json").write_text('{"first_seen": 5, "upgrades": "x"}')
    s = FileSeenStore(tmp_path / "seen.json")
    assert s.first_seen("p", "1") is None and s.due_for_cleanup(9, 2) == []


def test_file_mode_0600(tmp_path):
    s = FileSeenStore(tmp_path / "state" / "seen.json")
    s.record_first_seen("p", "1", T1)
    assert stat.S_IMODE(os.stat(tmp_path / "state" / "seen.json").st_mode) == 0o600


def test_reupgrade_rearms_cleanup(tmp_path):
    s = FileSeenStore(tmp_path / "seen.json")
    s.record_upgrade(1, ["a"])
    s.mark_cleaned(["a"])
    s.record_upgrade(3, ["a"])
    assert "a" not in s.due_for_cleanup(4, 2)
    assert "a" in s.due_for_cleanup(5, 2)


def test_cleanup_counts_from_latest_upgrade(tmp_path):
    s = FileSeenStore(tmp_path / "seen.json")
    s.record_upgrade(1, ["a"])
    s.record_upgrade(3, ["a"])
    assert "a" not in s.due_for_cleanup(4, 2)
    assert "a" in s.due_for_cleanup(5, 2)


def test_seed_never_overwrites_existing_hash(tmp_path):
    s = FileSeenStore(tmp_path / "seen.json")
    s.record_caveat_hash("a", "stored")
    assert seed_caveat_hashes(s, {"a": "changed text", "b": "new"}) == 1
    assert s.caveat_hash("a") == "stored" and s.caveat_hash("b") == caveat_hash("new")
