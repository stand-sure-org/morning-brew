import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest

from conftest import fixture_text
from morning_brew.brew.adapter import BrewCli
from morning_brew.brew.parse import parse_dry_run
from morning_brew.brew.runner import SubprocessRunner
from morning_brew.config import child_env
from morning_brew.domain import (
    BrewBusyError,
    BrewParseError,
    BrewUpdateError,
    CommandResult,
    InvalidPackageName,
)

BREW = "/opt/homebrew/bin/brew"
OK = CommandResult(0, "", "")


@dataclass
class RecordingRunner:
    """Records (argv, timeout); answers by argv prefix, else ``next``."""

    responses: dict[tuple[str, ...], CommandResult] = field(default_factory=dict)
    next: CommandResult = OK
    queue: list[CommandResult] = field(default_factory=list)
    calls: list[tuple[list[str], float]] = field(default_factory=list)

    def __call__(self, argv: Sequence[str], timeout: float) -> CommandResult:
        self.calls.append((list(argv), timeout))
        if self.queue:
            return self.queue.pop(0)
        for prefix, result in self.responses.items():
            if tuple(argv[1 : 1 + len(prefix)]) == prefix:
                return result
        return self.next


def fixture_responses(stderr: str = "") -> dict[tuple[str, ...], CommandResult]:
    return {
        ("outdated",): CommandResult(0, fixture_text("outdated.json"), stderr),
        ("info",): CommandResult(0, fixture_text("info.json"), stderr),
        ("leaves",): CommandResult(0, fixture_text("leaves.txt"), stderr),
        ("services",): CommandResult(0, fixture_text("services.json"), stderr),
        ("--version",): CommandResult(0, fixture_text("brew_version.txt"), stderr),
        ("upgrade", "--dry-run"): CommandResult(
            0, fixture_text("dry_run_upgrade.txt"), stderr
        ),
    }


def fixture_runner(stderr: str = "") -> RecordingRunner:
    return RecordingRunner(responses=fixture_responses(stderr))


def make_cli(runner: RecordingRunner, sleeps: list[float] | None = None) -> BrewCli:
    sink = sleeps if sleeps is not None else []
    return BrewCli(runner, sleep=sink.append)


# --- update -----------------------------------------------------------------


def test_update_retries_then_raises():
    sleeps: list[float] = []
    runner = RecordingRunner(next=CommandResult(1, "", "Error: network down"))
    with pytest.raises(BrewUpdateError):
        make_cli(runner, sleeps).update()
    assert sleeps == [5, 30]
    assert len(runner.calls) == 3
    assert all(argv == [BREW, "update"] for argv, _ in runner.calls)
    assert all(t == 120 for _, t in runner.calls)


def test_update_first_try_success_does_not_sleep():
    sleeps: list[float] = []
    runner = RecordingRunner()
    make_cli(runner, sleeps).update()
    assert sleeps == []
    assert len(runner.calls) == 1


def test_update_succeeds_on_third_attempt():
    sleeps: list[float] = []
    fail = CommandResult(1, "", "boom")
    runner = RecordingRunner(queue=[fail, fail, OK])
    make_cli(runner, sleeps).update()
    assert sleeps == [5, 30]
    assert len(runner.calls) == 3


# --- snapshot ---------------------------------------------------------------


def test_snapshot_ignores_stderr_noise_and_parses_fixtures():
    runner = fixture_runner(stderr=fixture_text("ngrok_stderr.txt"))
    s = make_cli(runner).snapshot()
    assert s.brew_version.startswith("Homebrew 7")
    assert s.candidates
    argvs = [argv for argv, _ in runner.calls]
    assert [BREW, "outdated", "--json=v2"] in argvs
    assert [BREW, "info", "--json=v2", "--installed"] in argvs
    assert [BREW, "leaves"] in argvs
    assert [BREW, "services", "list", "--json"] in argvs
    assert [BREW, "--version"] in argvs
    assert all(t == 120 for _, t in runner.calls)
    assert "awscli" in {c.full_name for c in s.candidates}


def test_unparseable_stdout_is_parse_error():
    runner = fixture_runner()
    runner.responses[("outdated",)] = CommandResult(0, "not json {", "")
    with pytest.raises(BrewParseError):
        make_cli(runner).snapshot()


@pytest.mark.parametrize("command", ["outdated", "info"])
def test_nonzero_read_command_is_parse_error(command):
    runner = fixture_runner()
    runner.responses[(command,)] = CommandResult(1, "", "Error: something")
    with pytest.raises(BrewParseError):
        make_cli(runner).snapshot()


@pytest.mark.parametrize(
    "info_doc",
    [
        {"formulae": [{"no_full_name": 1}], "casks": []},  # KeyError
        {"formulae": 5, "casks": []},  # TypeError
        {"formulae": [{"full_name": "a", "installed": ["x"]}], "casks": []},
        # ^ AttributeError: installed item is a str, not a mapping
    ],
)
def test_malformed_json_shape_is_parse_error(info_doc):
    runner = fixture_runner()
    runner.responses[("info",)] = CommandResult(0, json.dumps(info_doc), "")
    with pytest.raises(BrewParseError):
        make_cli(runner).snapshot()


def test_malformed_outdated_item_is_parse_error():
    runner = fixture_runner()
    runner.responses[("outdated",)] = CommandResult(
        0, json.dumps({"formulae": [{"oops": 1}], "casks": []}), ""
    )
    with pytest.raises(BrewParseError):
        make_cli(runner).snapshot()


# --- upgrade ----------------------------------------------------------------


def test_upgrade_argv_has_double_dash_and_names_only():
    runner = RecordingRunner()
    cli = make_cli(runner)
    result = cli.upgrade(["aws-c-auth", "aws-c-s3", "awscli"])
    assert runner.calls[-1][0] == [
        BREW,
        "upgrade",
        "--",
        "aws-c-auth",
        "aws-c-s3",
        "awscli",
    ]
    assert runner.calls[-1][1] == 1800
    assert result.ok is True


def test_invalid_name_never_reaches_runner():
    runner = RecordingRunner()
    cli = make_cli(runner)
    with pytest.raises(InvalidPackageName):
        cli.upgrade(["-x"])
    with pytest.raises(InvalidPackageName):
        cli.upgrade(["ok", "../etc"])
    with pytest.raises(InvalidPackageName):
        cli.dry_run_closure(["--greedy"])
    with pytest.raises(InvalidPackageName):
        cli.cleanup(["a b"])
    assert runner.calls == []


def test_upgrade_empty_makes_no_call():
    runner = RecordingRunner()
    with pytest.raises(InvalidPackageName):
        make_cli(runner).upgrade([])
    assert runner.calls == []


def test_upgrade_failure_returns_redacted_tail():
    runner = RecordingRunner(next=CommandResult(1, "", "Error: build failed\n"))
    result = make_cli(runner).upgrade(["a"])
    assert result.ok is False
    assert "build failed" in result.stderr_tail


def test_upgrade_stderr_tail_is_redacted():
    secret = "sk-" + "ant-" + "abcdefghijklmnopqrstuvwxyz0123"
    runner = RecordingRunner(next=CommandResult(1, "", f"token leak {secret}"))
    result = make_cli(runner).upgrade(["a"])
    assert secret not in result.stderr_tail


def test_upgrade_pinned_refusal_is_not_ok():
    stderr = "Error: Not upgrading 1 pinned package:\na 1.0 -> 1.1\n"
    for rc in (1, 0):
        runner = RecordingRunner(next=CommandResult(rc, "", stderr))
        result = make_cli(runner).upgrade(["a"])
        assert result.ok is False
        assert "pinned package" in result.stderr_tail


def test_upgrade_timeout_is_not_ok():
    runner = RecordingRunner(next=CommandResult(124, "", "timeout after 1800s"))
    assert make_cli(runner).upgrade(["a"]).ok is False


def test_brew_already_running_is_busy():  # Review Focus 2
    runner = RecordingRunner()
    runner.next = CommandResult(
        1, "", "Error: Another `brew upgrade` process is already running."
    )
    with pytest.raises(BrewBusyError):
        make_cli(runner).upgrade(["a"])


# --- dry run ----------------------------------------------------------------


def test_dry_run_closure_parses_fixture():
    runner = fixture_runner()
    cli = make_cli(runner)
    assert cli.dry_run_closure(["aws-c-auth"]) == frozenset(
        {"aws-c-auth", "aws-c-s3", "awscli"}
    )
    assert runner.calls[-1] == ([BREW, "upgrade", "--dry-run", "--", "aws-c-auth"], 120)


def test_parse_dry_run_real_fixture_headers():
    text = fixture_text("dry_run_upgrade.txt")
    assert "==> Would upgrade 2 dependents\n" in text  # no colon in the real header
    assert parse_dry_run(text) == frozenset({"aws-c-auth", "aws-c-s3", "awscli"})


def test_parse_dry_run_is_section_scoped():
    text = (
        "==> Would upgrade 1 outdated package:\n"
        "a 1.0 -> 1.1\n"
        "==> Would install 1 dependency\n"
        "z 0 -> 1\n"
        "==> Would upgrade 1 outdated dependencies:\n"
        "b  2.0  ->  2.1  (3MB)\n"
        "\n"
    )
    assert parse_dry_run(text) == frozenset({"a", "b"})


def test_parse_dry_run_ignores_lines_before_header():
    assert parse_dry_run("x 1 -> 2\n") == frozenset()
    assert parse_dry_run("") == frozenset()


def test_dry_run_failure_returns_empty_closure():
    runner = RecordingRunner(next=CommandResult(1, "", "already running"))
    assert make_cli(runner).dry_run_closure(["a"]) == frozenset()


def test_dry_run_empty_makes_no_call():
    runner = RecordingRunner()
    assert make_cli(runner).dry_run_closure([]) == frozenset()
    assert runner.calls == []


# --- cleanup ----------------------------------------------------------------


def test_cleanup_empty_makes_no_call():
    runner = RecordingRunner()
    make_cli(runner).cleanup([])
    assert runner.calls == []


def test_cleanup_argv_has_double_dash():
    runner = RecordingRunner()
    make_cli(runner).cleanup(["a", "b"])
    assert runner.calls == [([BREW, "cleanup", "--", "a", "b"], 120)]


# --- global invariants over a full exercise ---------------------------------


def test_never_pin_or_greedy_or_bare_upgrade():
    runner = fixture_runner()
    cli = make_cli(runner)
    cli.update()
    cli.snapshot()
    cli.dry_run_closure(["aws-c-auth"])
    cli.upgrade(["aws-c-auth", "aws-c-s3", "awscli"])
    cli.cleanup(["aws-c-auth"])
    cli.cleanup([])
    for bad in (["-x"], [], ["--greedy"]):
        with pytest.raises(InvalidPackageName):
            cli.upgrade(bad)
    argvs = [argv for argv, _ in runner.calls]
    assert any("upgrade" in a for a in argvs)
    for argv in argvs:
        assert argv[0] == BREW
        assert not {"pin", "unpin", "--greedy"} & set(argv), argv
        if argv[1] in ("upgrade", "cleanup"):
            assert "--" in argv, argv
            dash = argv.index("--")
            assert set(argv[2:dash]) <= {"--dry-run"}, argv
            assert len(argv) > dash + 1, argv  # never a bare upgrade/cleanup


# --- real subprocess runner -------------------------------------------------

SAFE_ENV = child_env({"PATH": "/usr/bin:/bin"})


def test_subprocess_runner_contract(tmp_path):
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "HOMEBREW_NO_AUTO_UPDATE": "1",
    }
    run = SubprocessRunner(env)
    # LC_CTYPE is set by CPython itself (PEP 538 locale coercion) and
    # __CF_USER_TEXT_ENCODING by macOS; neither comes from the runner.
    script = (
        "import json, os, sys; "
        "injected = {'LC_CTYPE', '__CF_USER_TEXT_ENCODING'}; "
        "print(json.dumps({'stdin': sys.stdin.read(), 'argv': sys.argv[1:], "
        "'env': sorted(k for k in os.environ if k not in injected)}))"
    )
    result = run([sys.executable, "-c", script, "$HOME", "a;echo b", "*"], 10)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["stdin"] == ""  # stdin is DEVNULL, not inherited
    assert out["argv"] == ["$HOME", "a;echo b", "*"]  # no shell expansion
    assert out["env"] == sorted(env)  # only the env we passed


def test_subprocess_runner_timeout():
    result = SubprocessRunner(SAFE_ENV)(
        [sys.executable, "-c", "import time; time.sleep(5)"], 0.2
    )
    assert result.returncode == 124
    assert result.stdout == ""
    assert "timeout after" in result.stderr


def test_subprocess_runner_missing_binary(tmp_path):
    result = SubprocessRunner(SAFE_ENV)([str(tmp_path / "nope")], 5)
    assert result.returncode == 127


def test_subprocess_runner_with_child_env_has_no_secrets():
    base = {"PATH": "/usr/bin", "HOME": "/h", "MY_" + "TOKEN": "x", "API_" + "KEY": "y"}
    env = child_env(base)
    assert not [k for k in env if "KEY" in k or "TOKEN" in k]
    result = SubprocessRunner(env)(
        [
            sys.executable,
            "-c",
            "import os; print(os.environ['HOMEBREW_NO_AUTO_UPDATE'])",
        ],
        10,
    )
    assert result.stdout.strip() == "1"


@pytest.mark.parametrize(
    "env",
    [{}, {"HOMEBREW_NO_AUTO_UPDATE": "0"}, {"PATH": "/usr/bin"}],
)
def test_subprocess_runner_refuses_env_without_no_auto_update(env):  # R37
    with pytest.raises(ValueError):
        SubprocessRunner(env)


def test_subprocess_runner_accepts_child_env():  # R37
    SubprocessRunner(child_env({}))  # does not raise


def test_subprocess_runner_survives_non_utf8_output():
    script = (
        "import sys; sys.stdout.buffer.write(bytes([255, 32]) + b'out'); "
        "sys.stderr.buffer.write(bytes([255, 32]) + b'err')"
    )
    result = SubprocessRunner(SAFE_ENV)([sys.executable, "-c", script], 10)
    assert result.returncode == 0
    assert result.stderr.endswith(" err")
    assert "\ufffd" in result.stderr
    assert result.stdout.endswith(" out")


def test_already_running_on_success_is_not_busy():
    runner = RecordingRunner(
        next=CommandResult(0, "", "Warning: another process is already running.")
    )
    result = make_cli(runner).upgrade(["a"])
    assert result.ok is True


def test_subprocess_runner_feeds_input_to_stdin():
    result = SubprocessRunner(SAFE_ENV)(
        [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
        10,
        input="hello",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HELLO"
