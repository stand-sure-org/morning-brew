"""File-backed StatusPort (T24): the last run summary, JSON 0600."""

import os
import stat

from morning_brew.config import load_config
from morning_brew.domain import Summary
from morning_brew.store.status import FileStatusStore

SUMMARY = Summary(
    run_id="20261117T080000Z",
    quality="degraded",
    upgraded=("a", "b"),
    failed=("c",),
    decided_holds=2,
    oldest_decided_hold_days=5,
    blocked_by_dep=1,
    transient=1,
    self_dep_commands=("brew upgrade -- fnox",),
    disagreements=("grype",),
    live_old_binary=(),
    anomalies=("x changed",),
    dead_man=False,
    degraded_reasons=("transient holds",),
)


def test_status_round_trip_and_mode(tmp_path):
    store = FileStatusStore(tmp_path / "status.json")
    store.write(SUMMARY)
    assert store.read() == SUMMARY
    assert stat.S_IMODE(os.stat(tmp_path / "status.json").st_mode) == 0o600


def test_status_missing_is_none(tmp_path):
    assert FileStatusStore(tmp_path / "status.json").read() is None


def test_status_corrupt_is_none(tmp_path):
    (tmp_path / "status.json").write_text('{"run_id": 3}')
    assert FileStatusStore(tmp_path / "status.json").read() is None
    (tmp_path / "status.json").write_text("{trunc")
    assert FileStatusStore(tmp_path / "status.json").read() is None


def test_config_notify_command(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('notify_command = ["/bin/hook", "-v"]\n')
    os.chmod(p, 0o600)
    assert load_config(p, state_dir=tmp_path).notify_command == ("/bin/hook", "-v")


def test_config_notify_command_rejects_bad_values(tmp_path):
    import pytest

    from morning_brew.domain import ConfigError

    for body in ("notify_command = []\n", 'notify_command = "x"\n'):
        p = tmp_path / "config.toml"
        p.write_text(body)
        os.chmod(p, 0o600)
        with pytest.raises(ConfigError):
            load_config(p, state_dir=tmp_path)
