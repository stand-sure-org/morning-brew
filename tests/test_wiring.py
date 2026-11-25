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
        self.timeouts: list[float] = []

    def __call__(
        self, argv: Any, timeout: float, input: str | None = None
    ) -> CommandResult:
        self.calls.append(list(argv))
        self.timeouts.append(timeout)
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

    def __init__(self, local: Any, claude: Any, mode: str, **kw: Any) -> None:
        self.local = local
        self.claude = claude
        self.mode = mode
        self.kw = kw
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
    # [R56] refused as live: built in shadow mode and flagged for `run`
    deps = build(tmp_path, 'chain_mode = "live"\n')
    assert deps.gate_locked is True
    assert deps.config.chain_mode == "shadow"
    assert [c.mode for c in SpyChain.instances] == ["shadow"]


def test_build_deps_refuses_live_without_gate_before_building_chain(tmp_path):
    build(tmp_path, 'chain_mode = "live"\n')
    assert "live" not in [c.mode for c in SpyChain.instances]


def test_gate_failure_uses_require_gate(tmp_path, monkeypatch):
    seen: list[str] = []

    def fake_require(config, gate):
        seen.append(config.chain_mode)
        raise GateNotPassed("no")

    monkeypatch.setattr(wiring, "require_gate", fake_require)
    deps = build(tmp_path, 'chain_mode = "live"\n')
    assert seen == ["live"] and deps.gate_locked


@pytest.mark.parametrize("cmd", [["status"], ["explain", "wget"]])
def test_read_commands_work_when_gate_locked(tmp_path, cmd, capsys):
    deps = build(tmp_path, 'chain_mode = "live"\n')
    rc = cli.main(cmd, deps_factory=lambda: deps)
    assert rc in (0, 2)  # explain of an unknown package is a usage error
    assert "GateNotPassed" not in capsys.readouterr().err


def test_eval_when_gate_locked_runs_the_shadow_chain(tmp_path, monkeypatch):
    got: list[Any] = []

    def fake_record(self, triage, audit, run_id, now):
        got.append(triage)
        return EvalReport(total=1, false_approves=(), missed_upgrades=())

    monkeypatch.setattr(GateStore, "record_eval", fake_record)
    monkeypatch.setattr(wiring, "eval_exit_code", lambda r, c: 0)
    deps = build(tmp_path, 'chain_mode = "live"\n')
    assert cli.main(["eval"], deps_factory=lambda: deps) == 0
    (triage,) = got
    assert triage.mode == "shadow"  # eval never approves live


def test_shadow_with_passing_gate_is_not_locked(tmp_path, monkeypatch):
    monkeypatch.setattr(GateStore, "passed", lambda self: True)
    assert build(tmp_path, 'chain_mode = "live"\n').gate_locked is False


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
    monkeypatch.setattr(
        wiring.launchd,
        "install",
        lambda *a, **k: got.append(("i", *a, k.get("start_interval"))),
    )
    monkeypatch.setattr(wiring.launchd, "uninstall", lambda *a: got.append(("u", *a)))
    runner = Runner()
    deps = build(tmp_path, runner=runner)
    assert deps.install() == 0 and deps.uninstall() == 0
    venv_bin = tmp_path / "proj" / ".venv" / "bin" / "morning-brew"
    home = Path(os.environ["HOME"])
    assert got == [
        ("i", runner, venv_bin, home, os.getuid(), 3600),
        ("u", runner, home, os.getuid()),
    ]


# --- status: shadow runs ------------------------------------------------------


def test_shadow_hook_reads_audit_log(tmp_path):
    deps = build(tmp_path)
    assert deps.shadow is not None
    assert deps.shadow().runs == 0


# --- I5 / R58: config keys reach their consumers -------------------------------


def test_start_interval_reaches_plist_and_install(tmp_path, monkeypatch):
    got: list[Any] = []
    monkeypatch.setattr(
        wiring.launchd, "install", lambda *a, **k: got.append(k["start_interval"])
    )
    deps = build(tmp_path, "start_interval = 900\n")
    plist = deps.print_plist().decode()
    assert "<key>StartInterval</key>" in plist and "<integer>900</integer>" in plist
    deps.install()
    assert got == [900]


def test_untrusted_text_cap_reaches_chain(tmp_path):
    build(tmp_path, "untrusted_text_cap = 1234\n")
    assert SpyChain.instances[0].kw["untrusted_cap"] == 1234


def test_ollama_num_predict_and_timeout_reach_client(tmp_path):
    build(
        tmp_path,
        'local_digest = "sha256:abc"\nlocal_num_predict = 77\nollama_timeout_s = 9\n',
    )
    local = SpyChain.instances[0].local
    assert isinstance(local, OllamaClient)
    assert local._num_predict == 77 and local._timeout == 9


def test_fnox_timeout_reaches_fnox_get(tmp_path):
    runner = Runner()
    build(tmp_path, "fnox_timeout_s = 3\n", runner=runner)
    (claude,) = SpyClaude.instances
    claude.key_provider()
    assert runner.timeouts == [3]


# --- I4: setup ------------------------------------------------------------------


def test_setup_binds_snapshot_caveats_prompt_and_ollama(tmp_path, monkeypatch):
    got: dict[str, Any] = {}

    def fake_setup(**kw: Any) -> int:
        got.update(kw)
        return 0

    monkeypatch.setattr(wiring, "setup_command", fake_setup)
    deps = build(tmp_path)
    assert deps.setup is not None and deps.setup() == 0
    assert got["snapshot"] == deps.ports.brew.snapshot
    assert got["installed_caveats"] == deps.ports.brew.installed_caveats
    assert got["seen"] is deps.ports.seen
    assert got["state_dir"] == state(tmp_path)
    assert got["ask"] is builtins.input and got["say"] is cli._emit
    assert got["model"] == "qwen3:4b"
    assert callable(got["ollama_digest"])


# --- I7: review holds the run lock -------------------------------------------------


def test_review_takes_the_run_lock(tmp_path, monkeypatch):
    from morning_brew.domain import LockHeld

    class HeldLock:
        def acquire(self):
            raise LockHeld("busy")

    monkeypatch.setattr(wiring, "review", lambda **kw: None)
    deps = build(tmp_path)
    deps.ports.lock = HeldLock()
    assert deps.review() == 75


# --- I9 / R57: weekly digest wired to the normal channel ---------------------------


def test_digest_port_is_wired_to_state_dir(tmp_path):
    from morning_brew.notify import DigestChannel

    deps = build(tmp_path)
    assert isinstance(deps.ports.digest, DigestChannel)
    assert deps.ports.digest._path == state(tmp_path) / "digest.json"
    assert deps.ports.digest.last_sent() is None


# --- targeted fix: review rules and the unjoinable memory are wired ------------


def test_review_gets_the_run_rules_on_its_snapshot(tmp_path, monkeypatch):
    from builders import make_candidate, make_snapshot

    got: dict[str, Any] = {}
    monkeypatch.setattr(wiring, "review", lambda **kw: got.update(kw))
    deps = build(tmp_path, 'trusted_taps = ["x/y"]\n')
    monkeypatch.setattr(deps.ports.live, "live_set", lambda snap: frozenset())
    deps.review()
    snap = make_snapshot(make_candidate("evil", tap="bad/tap"))
    (d,) = got["rules"](snap)
    assert d.rule_id == "R06"


def test_unjoinable_memory_is_wired_to_state_dir(tmp_path):
    from morning_brew.store.unjoinable import FileUnjoinableStore

    deps = build(tmp_path)
    store = deps.ports.unjoinable_seen
    assert isinstance(store, FileUnjoinableStore)
    assert store.read() == frozenset()
    store.write(frozenset({"ghost"}))
    assert FileUnjoinableStore(state(tmp_path) / "unjoinable.json").read() == {"ghost"}
    (state(tmp_path) / "unjoinable.json").write_text('{"not": "a list"}')
    assert store.read() == frozenset()
