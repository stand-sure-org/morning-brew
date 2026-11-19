import os
from pathlib import Path

import pytest

from morning_brew.config import Config, child_env, detect_self_deps, load_config
from morning_brew.domain import ConfigError, ConfigPermissionError


def _write(tmp_path: Path, text: str, mode: int = 0o600) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(text)
    p.chmod(mode)
    return p


def test_child_env_drops_secrets_and_keeps_brew_path():
    key = "MY_API_" + "KEY"
    tok = "GH_" + "TOKEN"
    sec = "DB_" + "SECRET"
    e = child_env({"PATH": "/usr/bin", "HOME": "/h", key: "x", tok: "y", sec: "z"})
    assert e["PATH"].startswith("/opt/homebrew/bin")
    assert e["HOMEBREW_NO_INSTALL_CLEANUP"] == "1"
    assert not any("KEY" in k or "TOKEN" in k or "SECRET" in k for k in e)
    assert "HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK" not in e
    assert child_env({})["PATH"].startswith("/opt/homebrew/bin")


def test_child_env_exact_allowlist():
    e = child_env({"PATH": "/usr/bin", "HOME": "/h", "FOO": "1", "SSH_AUTH_SOCK": "/s"})
    assert set(e) == {
        "PATH",
        "HOME",
        "HOMEBREW_NO_AUTO_UPDATE",
        "HOMEBREW_NO_ANALYTICS",
        "HOMEBREW_NO_ENV_HINTS",
        "HOMEBREW_NO_INSTALL_CLEANUP",
    }
    assert e["HOME"] == "/h"
    assert e["HOMEBREW_NO_AUTO_UPDATE"] == "1"
    assert e["HOMEBREW_NO_ANALYTICS"] == "1"
    assert e["HOMEBREW_NO_ENV_HINTS"] == "1"


def test_child_env_passthrough_optionals():
    base = {
        "LANG": "en_US.UTF-8",
        "LC_ALL": "C",
        "TMPDIR": "/t",
        "USER": "u",
        "LOGNAME": "u",
    }
    e = child_env(base)
    for k, v in base.items():
        assert e[k] == v


def test_child_env_path_dedup_and_brew_first():
    e = child_env({"PATH": "/usr/bin:/opt/homebrew/bin:/usr/bin:/bin"})
    assert e["PATH"] == "/opt/homebrew/bin:/usr/bin:/bin"


def test_child_env_no_home_when_absent():
    assert "HOME" not in child_env({})


def test_group_writable_config_refused(tmp_path):
    p = _write(tmp_path, "", 0o664)
    with pytest.raises(ConfigPermissionError):
        load_config(p, state_dir=tmp_path)


def test_world_writable_config_refused(tmp_path):
    p = _write(tmp_path, "", 0o602)
    with pytest.raises(ConfigPermissionError):
        load_config(p, state_dir=tmp_path)


def test_foreign_owner_refused(tmp_path, monkeypatch):
    p = _write(tmp_path, "")
    monkeypatch.setattr(os, "getuid", lambda: os.stat(p).st_uid + 1)
    with pytest.raises(ConfigPermissionError):
        load_config(p, state_dir=tmp_path)


def test_valid_config_loads_and_unknown_key_rejected(tmp_path):
    p = _write(tmp_path, 'chain_mode = "live"\ntrusted_taps = ["a/b"]\n')
    cfg = load_config(p, state_dir=tmp_path)
    assert cfg.chain_mode == "live"
    assert cfg.trusted_taps == frozenset({"a/b"})
    assert cfg.state_dir == tmp_path
    p = _write(tmp_path, "bogus = 1\n")
    with pytest.raises(ConfigError, match="bogus"):
        load_config(p, state_dir=tmp_path)


def test_trusted_taps_lowercased(tmp_path):
    p = _write(tmp_path, 'trusted_taps = ["Homebrew/Core", "Foo/Bar"]\n')
    assert load_config(p, state_dir=tmp_path).trusted_taps == frozenset(
        {"homebrew/core", "foo/bar"}
    )


def test_state_dir_in_toml_rejected(tmp_path):
    p = _write(tmp_path, 'state_dir = "/x"\n')
    with pytest.raises(ConfigError, match="state_dir"):
        load_config(p, state_dir=tmp_path)


@pytest.mark.parametrize(
    "text",
    [
        'chain_mode = "maybe"\n',
        'min_age_days = "3"\n',
        "min_age_days = true\n",
        'trusted_taps = "a/b"\n',
        "trusted_taps = [1]\n",
        "quiet_hours = [1]\n",
        "quiet_hours = [1, 24]\n",
        'quiet_hours = ["a", "b"]\n',
        "local_digest = 5\n",
        "this is not toml\n",
    ],
)
def test_wrong_values_rejected(tmp_path, text):
    p = _write(tmp_path, text)
    with pytest.raises(ConfigError):
        load_config(p, state_dir=tmp_path)


def test_quiet_hours_and_scalars_load(tmp_path):
    p = _write(
        tmp_path,
        'quiet_hours = [23, 6]\nmin_age_days = 5\nlocal_digest = "sha256:ab"\n',
    )
    cfg = load_config(p, state_dir=tmp_path)
    assert cfg.quiet_hours == (23, 6)
    assert cfg.min_age_days == 5
    assert cfg.local_digest == "sha256:ab"


def test_fnox_config_path_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    p = _write(tmp_path, 'fnox_config = "~/proj/fnox.toml"\n')
    cfg = load_config(p, state_dir=tmp_path)
    assert cfg.fnox_config == tmp_path / "proj" / "fnox.toml"
    assert load_config(None, state_dir=tmp_path).fnox_config is None


def test_defaults(tmp_path):
    cfg = load_config(None, state_dir=tmp_path)
    assert cfg.min_age_days == 3
    assert cfg.chain_mode == "shadow"
    assert cfg == Config(state_dir=tmp_path)


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml", state_dir=tmp_path) == Config(
        state_dir=tmp_path
    )


def test_self_deps():
    assert detect_self_deps(
        Path("/opt/homebrew/Cellar/python@3.14/3.14.0/bin/python3")
    ) == {
        "fnox",
        "python@3.14",
    }


def test_self_deps_opt_path_any_prefix():
    assert detect_self_deps(Path("/usr/local/opt/python@3.12/bin/python3")) == {
        "fnox",
        "python@3.12",
    }


def test_self_deps_plain_python_only_fnox():
    assert detect_self_deps(Path("/usr/bin/python3")) == {"fnox"}


def test_self_deps_venv_symlink_chain(tmp_path):
    real = (
        tmp_path / "prefix" / "Cellar" / "python@3.14" / "3.14.0" / "bin" / "python3.14"
    )
    real.parent.mkdir(parents=True)
    real.write_text("")
    hop = tmp_path / "prefix" / "bin" / "python3"
    hop.parent.mkdir()
    hop.symlink_to(real)
    venv_py = tmp_path / "proj" / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.symlink_to(hop)
    assert detect_self_deps(venv_py) == {"fnox", "python@3.14"}
