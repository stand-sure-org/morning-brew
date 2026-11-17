"""Composition root (T24). Every external effect is faked: no brew, osascript,
fnox, network or launchctl runs here."""

import builtins
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from morning_brew import cli, reconcile, wiring
from morning_brew.brew.adapter import BrewCli
from morning_brew.components import plan_components
from morning_brew.config import detect_self_deps
from morning_brew.domain import CommandResult, GateNotPassed
from morning_brew.liveprocess import LiveProcessScanner
from morning_brew.notify import (
    CommandNotifier,
    FanOutNotifier,
    NotificationNotifier,
    SayNotifier,
)
from morning_brew.rules import evaluate_all
from morning_brew.triage.eval import EvalReport, GateStore
from morning_brew.triage.ollama import OllamaClient

FAKE_SECRET_NAME = "_".join(["MB", "TEST", "API", "KEY"])
FAKE_TOKEN_NAME = "_".join(["MB", "GH", "TOKEN"])


class Runner:
    """CommandRunner fake: records argv, never starts a process."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: Any, timeout: float, input: str | None = None
    ) -> CommandResult:
        self.calls.append(list(argv))
        return CommandResult(0, "value\n", "")


class SpyClaude:
    instances: list["SpyClaude"] = []

    def __init__(self, model: str, effort: str, key_provider: Any) -> None:
        self.model = model
        self.effort = effort
        self.key_provider = key_provider
        self.last_error_kind: str | None = None
        SpyClaude.instances.append(self)


class SpyChain:
    instances: list["SpyChain"] = []

    def __init__(self, local: Any, claude: Any, mode: str) -> None:
        self.local = local
        self.claude = claude
        self.mode = mode
        SpyChain.instances.append(self)


@pytest.fixture(autouse=True)
def spies(monkeypatch, tmp_path):
    SpyClaude.instances = []
    SpyChain.instances = []
    monkeypatch.setattr(wiring, "ClaudeClient", SpyClaude)
    monkeypatch.setattr(wiring, "TriageChain", SpyChain)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    venv = tmp_path / "proj" / ".venv"
    (venv / "bin").mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(venv / "bin" / "python"))
    monkeypatch.setattr(sys, "prefix", str(venv))


def state(tmp_path: Path) -> Path:
    return tmp_path / "state"


def write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body)
    os.chmod(path, 0o600)
    return path


def build(tmp_path: Path, body: str = "", runner: Runner | None = None, **kw: Any):
    cfg = write_config(tmp_path, body) if body else None
    return wiring.build_deps(
        cfg, state_dir=state(tmp_path), runner=runner or Runner(), **kw
    )


# --- C11: the gate is checked before the chain exists -----------------------


def test_build_deps_refuses_live_without_gate(tmp_path):
    with pytest.raises(GateNotPassed):
        build(tmp_path, 'chain_mode = "live"\n')


def test_build_deps_refuses_live_without_gate_before_building_chain(tmp_path):
    with pytest.raises(GateNotPassed):
        build(tmp_path, 'chain_mode = "live"\n')
    assert SpyChain.instances == []


def test_build_deps_live_with_passing_gate_builds_live_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(GateStore, "passed", lambda self: True)
    build(tmp_path, 'chain_mode = "live"\n')
    assert [c.mode for c in SpyChain.instances] == ["live"]


def test_shadow_mode_needs_no_gate(tmp_path):
    build(tmp_path)
    assert [c.mode for c in SpyChain.instances] == ["shadow"]


# --- C12: exactly one ClaudeClient, surfaced through the CLI hook ------------


def test_exactly_one_claude_client_shared_by_chain_and_error_hook(tmp_path):
    deps = build(tmp_path)
    (claude,) = SpyClaude.instances
    (chain,) = SpyChain.instances
    assert chain.claude is claude
    assert deps.ports.triage is chain
    assert deps.triage_error is not None and deps.triage_error() is None
    claude.last_error_kind = "auth"
    assert deps.triage_error() == "auth"


def test_claude_model_and_effort_from_config(tmp_path):
    build(tmp_path, 'claude_model = "claude-x"\nclaude_effort = "medium"\n')
    (claude,) = SpyClaude.instances
    assert (claude.model, claude.effort) == ("claude-x", "medium")


# --- R19: fnox config next to the project, key fetched lazily ----------------


def test_fnox_config_defaults_to_project_root_and_key_provider_uses_it(tmp_path):
    runner = Runner()
    deps = build(tmp_path, runner=runner)
    expected = tmp_path / "proj" / "fnox.toml"
    assert deps.config.fnox_config == expected
    assert runner.calls == []  # lazy: nothing fetched while wiring
    (claude,) = SpyClaude.instances
    assert claude.key_provider() == "value"
    assert runner.calls == [
        ["fnox", "get", "--config", str(expected), "ANTHROPIC_API_KEY"]
    ]


def test_explicit_fnox_config_is_kept(tmp_path):
    other = tmp_path / "elsewhere" / "fnox.toml"
    deps = build(tmp_path, f'fnox_config = "{other}"\n')
    assert deps.config.fnox_config == other


# --- R48: the local stage only with a pinned digest --------------------------


def test_local_stage_absent_without_digest(tmp_path):
    build(tmp_path)
    assert SpyChain.instances[0].local is None


def test_local_stage_present_with_digest(tmp_path):
    build(tmp_path, 'local_digest = "sha256:abc123"\n')
    assert isinstance(SpyChain.instances[0].local, OllamaClient)


# --- C4: tz-aware clock -------------------------------------------------------


def test_clock_is_timezone_aware(tmp_path):
    now = build(tmp_path).ports.clock()
    assert now.tzinfo is not None and now.utcoffset() is not None


# --- C6: state root 0700 ------------------------------------------------------


def test_state_root_created_0700(tmp_path):
    build(tmp_path)
    assert stat.S_IMODE(state(tmp_path).stat().st_mode) == 0o700


def test_state_root_tightened_to_0700(tmp_path):
    state(tmp_path).mkdir(mode=0o755)
    os.chmod(state(tmp_path), 0o755)
    build(tmp_path)
    assert stat.S_IMODE(state(tmp_path).stat().st_mode) == 0o700


def test_default_state_dir_is_under_home(tmp_path, monkeypatch):
    deps = wiring.build_deps(None, runner=Runner())
    expected = Path(os.environ["HOME"]) / ".local" / "state" / "morning-brew"
    assert deps.config.state_dir == expected
    assert stat.S_IMODE(expected.stat().st_mode) == 0o700


# --- C7: one allowlisted-env SubprocessRunner --------------------------------


def test_default_runner_is_subprocess_runner_with_child_env(tmp_path, monkeypatch):
    seen: list[dict[str, str]] = []

    class SpyRunner(Runner):
        def __init__(self, env: dict[str, str]) -> None:
            super().__init__()
            seen.append(dict(env))

    monkeypatch.setattr(wiring, "SubprocessRunner", SpyRunner)
    monkeypatch.setenv(FAKE_SECRET_NAME, "x")
    monkeypatch.setenv(FAKE_TOKEN_NAME, "y")
    deps = wiring.build_deps(None, state_dir=state(tmp_path))
    (env,) = seen
    assert env["HOMEBREW_NO_AUTO_UPDATE"] == "1"
    assert env["PATH"].startswith("/opt/homebrew/bin")
    assert not [k for k in env if "KEY" in k or "TOKEN" in k]
    assert isinstance(deps.ports.brew, BrewCli)
    assert isinstance(deps.ports.brew._run, SpyRunner)


# --- R13: self-deps from the running interpreter -----------------------------


def test_self_deps_detected_from_sys_executable(tmp_path, monkeypatch):
    exe = "/opt/homebrew/Cellar/python@3.13/3.13.1/bin/python3.13"
    monkeypatch.setattr(sys, "executable", exe)
    deps = build(tmp_path)
    assert deps.ports.self_deps == detect_self_deps(Path(exe))
    assert "python@3.13" in deps.ports.self_deps and "fnox" in deps.ports.self_deps


# --- C13: trusted taps = config | setup file ---------------------------------


def test_effective_trusted_taps_union(tmp_path):
    state(tmp_path).mkdir()
    (state(tmp_path) / "trusted_taps.json").write_text(json.dumps(["Foo/Bar"]))
    deps = build(tmp_path, 'trusted_taps = ["a/b"]\n')
    assert deps.config.trusted_taps == frozenset({"a/b", "foo/bar"})


# --- C14: notifier fan-out ----------------------------------------------------


def test_notifier_fan_out_without_command(tmp_path):
    deps = build(tmp_path, "quiet_hours = [23, 6]\n")
    n = deps.ports.notifier
    assert isinstance(n, FanOutNotifier)
    assert [type(x) for x in n._normal] == [NotificationNotifier]
    assert [type(x) for x in n._urgent] == [SayNotifier]
    assert n._quiet == (23, 6)
    assert n._store == state(tmp_path) / "urgent_throttle.json"
    assert n._clock().tzinfo is not None


def test_notifier_fan_out_with_command(tmp_path):
    deps = build(tmp_path, 'notify_command = ["/usr/local/bin/hook", "--x"]\n')
    urgent = deps.ports.notifier._urgent
    assert [type(x) for x in urgent] == [SayNotifier, CommandNotifier]
    assert urgent[1]._argv == ["/usr/local/bin/hook", "--x"]


# --- live set, rules, components, reconcile, stores ---------------------------


def test_ports_bind_real_functions_and_stores(tmp_path):
    deps = build(tmp_path)
    p = deps.ports
    assert isinstance(p.live, LiveProcessScanner)
    assert p.live._home == Path(os.environ["HOME"])
    assert p.rules is evaluate_all
    assert p.components is plan_components
    assert p.reconcile is reconcile.reconcile
    assert p.status.read() is None
    assert p.stamp.read() is None
    assert p.holds.list_all() == []


# --- C15: review binding ------------------------------------------------------


def test_review_binds_self_deps_input_and_cli_emit(tmp_path, monkeypatch):
    got: dict[str, Any] = {}

    def fake_review(*args: Any, **kwargs: Any) -> None:
        got["args"], got["kwargs"] = args, kwargs

    monkeypatch.setattr(wiring, "review", fake_review)
    deps = build(tmp_path)
    assert deps.review() == 0
    kw = got["kwargs"]
    assert kw["self_deps"] == deps.ports.self_deps
    assert kw["ask"] is builtins.input
    assert kw["say"] is cli._emit
    assert kw["now"].tzinfo is not None
    assert kw["holds"] is deps.ports.holds and kw["brew"] is deps.ports.brew
    assert kw["components"] is plan_components and kw["live"] is deps.ports.live


# --- C8/C9: eval records the gate and exits on that report --------------------


def test_eval_records_gate_and_exit_code_comes_from_that_report(tmp_path, monkeypatch):
    report = EvalReport(total=1, false_approves=(), missed_upgrades=())
    recorded: list[Any] = []

    def fake_record(self, triage, audit, run_id, now):
        recorded.append((self, triage, audit, run_id, now))
        return report

    seen: list[Any] = []

    def fake_exit(r, cases):
        seen.append((r, cases))
        return 7

    monkeypatch.setattr(GateStore, "record_eval", fake_record)
    monkeypatch.setattr(wiring, "eval_exit_code", fake_exit)
    monkeypatch.setattr(wiring, "load_cases", lambda: ["cases"])
    deps = build(tmp_path)
    assert deps.eval() == 7
    ((gate, triage, audit, run_id, now),) = recorded
    assert gate._path == state(tmp_path) / "gate.json"
    assert triage is deps.ports.triage and audit is deps.ports.audit
    assert run_id.startswith("eval-") and now.tzinfo is not None
    assert seen == [(report, ["cases"])]


# --- T21: plist, install, uninstall -------------------------------------------


def test_print_plist_points_at_venv_binary(tmp_path):
    deps = build(tmp_path)
    plist = deps.print_plist().decode()
    venv_bin = tmp_path / "proj" / ".venv" / "bin" / "morning-brew"
    assert f"<string>{venv_bin}</string>" in plist
    assert "RunAtLoad" not in plist


def test_install_and_uninstall_bind_launchd_with_uid(tmp_path, monkeypatch):
    got: list[tuple] = []
    monkeypatch.setattr(wiring.launchd, "install", lambda *a: got.append(("i", *a)))
    monkeypatch.setattr(wiring.launchd, "uninstall", lambda *a: got.append(("u", *a)))
    runner = Runner()
    deps = build(tmp_path, runner=runner)
    assert deps.install() == 0 and deps.uninstall() == 0
    venv_bin = tmp_path / "proj" / ".venv" / "bin" / "morning-brew"
    home = Path(os.environ["HOME"])
    assert got == [
        ("i", runner, venv_bin, home, os.getuid()),
        ("u", runner, home, os.getuid()),
    ]


# --- status: shadow runs ------------------------------------------------------


def test_shadow_hook_reads_audit_log(tmp_path):
    deps = build(tmp_path)
    assert deps.shadow is not None
    assert deps.shadow().runs == 0
