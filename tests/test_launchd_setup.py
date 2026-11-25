import json
import os
import plistlib
import stat
from pathlib import Path

import pytest

from builders import make_candidate, make_snapshot
from fakes import FakeSeen
from morning_brew.domain import CommandResult, InstallError
from morning_brew.launchd import install, render_plist, uninstall
from morning_brew.setup import read_trusted_taps, run_setup


class Runner:
    def __init__(self, rc: int = 0, stderr: str = ""):
        self.calls: list[tuple[list[str], float]] = []
        self.rc = rc
        self.stderr = stderr

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        return CommandResult(self.rc, "", self.stderr)


def _venv(tmp_path: Path) -> Path:
    b = tmp_path / ".venv" / "bin" / "morning-brew"
    b.parent.mkdir(parents=True)
    b.write_text("#!/bin/sh\n")
    b.chmod(0o755)
    return b


def test_plist_contents():
    p = plistlib.loads(
        render_plist(Path("/p/.venv/bin/morning-brew"), Path("/Users/me"))
    )
    assert (
        p["StartInterval"] == 3600
        and "RunAtLoad" not in p
        and p["ProgramArguments"] == ["/p/.venv/bin/morning-brew", "run"]
    )
    assert "fnox" not in " ".join(p["ProgramArguments"])
    assert p["EnvironmentVariables"]["PATH"].startswith("/opt/homebrew/bin")
    assert p["EnvironmentVariables"]["HOME"] == "/Users/me"
    assert p["EnvironmentVariables"]["HOMEBREW_NO_AUTO_UPDATE"] == "1"
    assert "WorkingDirectory" not in p
    assert p["StandardOutPath"].endswith("Library/Logs/morning-brew/stdout.log")
    assert p["StandardErrorPath"].endswith("Library/Logs/morning-brew/stderr.log")
    assert p["ProcessType"] == "Background" and p["Label"] == "com.morningbrew.run"


def test_install_and_uninstall_argv(tmp_path):
    venv = _venv(tmp_path)
    runner, runner_u = Runner(), Runner()
    install(runner, venv, tmp_path, 501)
    plist = tmp_path / "Library/LaunchAgents/com.morningbrew.run.plist"
    assert runner.calls[0][0] == ["launchctl", "bootout", "gui/501/com.morningbrew.run"]
    assert runner.calls[1][0] == ["launchctl", "bootstrap", "gui/501", str(plist)]
    assert all(t > 0 for _, t in runner.calls)
    assert stat.S_IMODE(plist.stat().st_mode) == 0o644
    logs = tmp_path / "Library/Logs/morning-brew"
    assert stat.S_IMODE(logs.stat().st_mode) == 0o700
    uninstall(runner_u, tmp_path, 501)
    assert runner_u.calls[0][0][:3] == [
        "launchctl",
        "bootout",
        "gui/501/com.morningbrew.run",
    ]
    assert not plist.exists()
    uninstall(Runner(), tmp_path, 501)  # missing plist is fine


def test_install_ignores_not_loaded_bootout(tmp_path):
    venv = _venv(tmp_path)

    class R(Runner):
        def __call__(self, argv, timeout):
            self.calls.append((list(argv), timeout))
            if argv[1] == "bootout":
                return CommandResult(3, "", "Could not find service")
            return CommandResult(0, "", "")

    install(R(), venv, tmp_path, 501)


def test_install_bootstrap_failure_raises(tmp_path):
    venv = _venv(tmp_path)

    class R(Runner):
        def __call__(self, argv, timeout):
            self.calls.append((list(argv), timeout))
            return CommandResult(5 if argv[1] == "bootstrap" else 0, "", "boom")

    with pytest.raises(InstallError):
        install(R(), venv, tmp_path, 501)


def test_install_requires_built_venv(tmp_path):
    runner = Runner()
    with pytest.raises(InstallError):
        install(runner, tmp_path / "missing", tmp_path, 501)
    assert runner.calls == []
    nonexec = tmp_path / "plain"
    nonexec.write_text("x")
    nonexec.chmod(0o644)
    with pytest.raises(InstallError):
        install(runner, nonexec, tmp_path, 501)


def _snap():
    return make_snapshot(
        make_candidate("oh-my-posh", tap="JanDeDobbeleer/oh-my-posh"),
        make_candidate("awscli", tap="homebrew/core"),
    )


def test_setup_declined_changes_nothing(tmp_path):
    seen = FakeSeen()
    texts = []
    r = run_setup(
        _snap(),
        {"awscli": "caveat"},
        seen,
        tmp_path / "state",
        lambda t: texts.append(t) or False,
    )
    assert r.trusted_taps == frozenset() and r.seeded == 0
    assert not (tmp_path / "state" / "trusted_taps.json").exists()
    assert seen.caveat_hash("awscli") is None
    assert "jandedobbeleer/oh-my-posh" in texts[0] and "1" in texts[0]


def test_setup_confirmed_seeds_taps_and_hashes(tmp_path):
    seen = FakeSeen()
    d = tmp_path / "state"
    r = run_setup(_snap(), {"awscli": "caveat"}, seen, d, lambda t: True)
    assert read_trusted_taps(d) == {"jandedobbeleer/oh-my-posh"}
    assert seen.caveat_hash("awscli") is not None
    assert r.seeded == 1 and r.trusted_taps == {"jandedobbeleer/oh-my-posh"}
    f = d / "trusted_taps.json"
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert json.loads(f.read_text()) == ["jandedobbeleer/oh-my-posh"]


def test_setup_keeps_existing_hash_and_existing_taps(tmp_path):
    seen = FakeSeen()
    seen.record_caveat_hash("awscli", "old")
    d = tmp_path / "state"
    run_setup(_snap(), {"awscli": "new"}, seen, d, lambda t: True)
    assert seen.caveat_hash("awscli") == "old"


def test_confirmation_includes_digest_hint(tmp_path):
    texts = []
    run_setup(
        _snap(),
        {},
        FakeSeen(),
        tmp_path,
        lambda t: texts.append(t) or False,
        digest_hint="set local_digest = sha256:abc",
    )
    assert "sha256:abc" in texts[0]


def test_read_trusted_taps_tolerates_missing_and_corrupt(tmp_path):
    assert read_trusted_taps(tmp_path) == frozenset()
    (tmp_path / "trusted_taps.json").write_text("{not json")
    assert read_trusted_taps(tmp_path) == frozenset()
    (tmp_path / "trusted_taps.json").write_text('{"a": 1}')
    assert read_trusted_taps(tmp_path) == frozenset()


def test_plist_start_interval_is_configurable(tmp_path):
    p = plistlib.loads(
        render_plist(Path("/p/.venv/bin/morning-brew"), Path("/Users/me"), 900)
    )
    assert p["StartInterval"] == 900
    venv = _venv(tmp_path)
    install(Runner(), venv, tmp_path, 501, start_interval=600)
    written = tmp_path / "Library/LaunchAgents/com.morningbrew.run.plist"
    assert plistlib.loads(written.read_bytes())["StartInterval"] == 600


# --- I4: the `setup` command ----------------------------------------------------


def _setup(tmp_path, answers, digest=None, digest_exc=None):
    from morning_brew.setup import setup_command

    out: list[str] = []
    asked: list[str] = []
    it = iter(answers)

    def ask(prompt: str) -> str:
        asked.append(prompt)
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    def probe():
        if digest_exc is not None:
            raise digest_exc
        return digest

    seen = FakeSeen()
    rc = setup_command(
        snapshot=_snap,
        installed_caveats=lambda: {"awscli": "caveat"},
        seen=seen,
        state_dir=tmp_path / "state",
        ask=ask,
        say=out.append,
        ollama_digest=probe,
        model="qwen3:4b",
    )
    return rc, out, asked, seen


def test_setup_command_confirmed_with_y(tmp_path):
    rc, out, asked, seen = _setup(tmp_path, ["y"])
    assert rc == 0
    assert read_trusted_taps(tmp_path / "state") == {"jandedobbeleer/oh-my-posh"}
    assert seen.caveat_hash("awscli") is not None
    assert "[y/N]" in asked[0]
    assert any("jandedobbeleer/oh-my-posh" in line for line in out)


@pytest.mark.parametrize("answers", [[""], ["n"], ["yes please"], []])
def test_setup_command_anything_but_y_declines(tmp_path, answers):
    rc, out, _, seen = _setup(tmp_path, answers)
    assert rc == 0
    assert not (tmp_path / "state" / "trusted_taps.json").exists()
    assert seen.caveat_hash("awscli") is None
    assert any("nothing written" in line for line in out)


def test_setup_command_prints_ollama_digest_hint_when_up(tmp_path):
    _, out, _, _ = _setup(tmp_path, ["n"], digest="b" * 64)
    text = "\n".join(out)
    assert "local_digest" in text and "sha256:" + "b" * 64 in text


def test_setup_command_no_hint_when_ollama_down(tmp_path):
    from morning_brew.domain import ModelUnavailable

    _, out, _, _ = _setup(tmp_path, ["n"], digest_exc=ModelUnavailable("down"))
    assert "local_digest" not in "\n".join(out)
