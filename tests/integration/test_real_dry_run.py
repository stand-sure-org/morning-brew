"""Real read-only dry run against this machine's brew (T24).

Deselected by default (``addopts = -m "not integration"`` in pyproject). When
run, every child process goes through a recording wrapper around the real
runner. Only an ALLOWLIST of read-only argv is ever executed [I8]; anything
else (a mutation, osascript, say, fnox, launchctl, an unknown command) is
recorded, never run, and fails the test. State goes to a temporary directory
and the config is an explicit temporary file, so the owner's real state and
``~/.config/morning-brew/config.toml`` are never read or touched. With fresh
state everything is first seen today, so the soak rule defers it and no model
is called.
"""

import os
from collections.abc import Sequence
from pathlib import Path

import pytest

from morning_brew.brew.runner import SubprocessRunner
from morning_brew.cli import CliDeps, main
from morning_brew.config import child_env
from morning_brew.domain import CommandResult

_BREW = "/opt/homebrew/bin/brew"
# Exact read-only argv the tool issues (brew/adapter.py, liveprocess.py,
# triage/ollama.py). A prefix entry ends with "--": names may follow it.
_READ_ONLY: tuple[tuple[str, ...], ...] = (
    (_BREW, "outdated", "--json=v2"),
    (_BREW, "info", "--json=v2", "--installed"),
    (_BREW, "leaves"),
    (_BREW, "services", "list", "--json"),
    (_BREW, "--version"),
    ("ps", "-axo", "ppid=,comm="),
    ("lsof", "-nP", "-iTCP:11434", "-sTCP:LISTEN"),
)
_READ_ONLY_PREFIXES: tuple[tuple[str, ...], ...] = (
    (_BREW, "upgrade", "--dry-run", "--"),
)


def is_read_only(argv: Sequence[str]) -> bool:
    args = tuple(argv)
    if args in _READ_ONLY:
        return True
    return any(args[: len(p)] == p and len(args) > len(p) for p in _READ_ONLY_PREFIXES)


def is_mutating(argv: Sequence[str]) -> bool:
    """Allowlist: anything not known to be read-only counts as mutating."""
    return not is_read_only(argv)


class RecordingRunner:
    def __init__(self, inner: SubprocessRunner) -> None:
        self._inner = inner
        self.calls: list[list[str]] = []

    @property
    def mutating_calls(self) -> list[list[str]]:
        return [c for c in self.calls if is_mutating(c)]

    def __call__(
        self, argv: Sequence[str], timeout: float, input: str | None = None
    ) -> CommandResult:
        self.calls.append(list(argv))
        if is_mutating(argv):  # never executed
            return CommandResult(1, "", "blocked by test")
        return self._inner(argv, timeout, input=input)


@pytest.fixture
def recording_runner() -> RecordingRunner:
    return RecordingRunner(SubprocessRunner(child_env(os.environ)))


def hermetic_config(tmp_path: Path) -> Path:
    """An explicit config file, so the owner's real one is never loaded."""
    path = tmp_path / "config.toml"
    path.write_text('chain_mode = "shadow"\n', encoding="utf-8")
    os.chmod(path, 0o600)
    return path


@pytest.fixture
def real_deps(tmp_path: Path, recording_runner: RecordingRunner) -> CliDeps:
    from morning_brew.wiring import build_deps

    return build_deps(
        hermetic_config(tmp_path),
        state_dir=tmp_path / "state",
        runner=recording_runner,
    )


def test_is_mutating_classifier():
    b = _BREW
    for argv in (
        [b, "outdated", "--json=v2"],
        [b, "info", "--json=v2", "--installed"],
        [b, "leaves"],
        [b, "services", "list", "--json"],
        [b, "--version"],
        [b, "upgrade", "--dry-run", "--", "x"],
        ["ps", "-axo", "ppid=,comm="],
        ["lsof", "-nP", "-iTCP:11434", "-sTCP:LISTEN"],
    ):
        assert not is_mutating(argv), argv
    # an allowlist: everything else counts, known-bad or merely unknown
    for argv in (
        [b, "update"],
        [b, "upgrade", "--", "wget"],
        [b, "upgrade", "--dry-run"],  # no names: never issued, not allowed
        [b, "upgrade", "--dry-run", "--greedy", "--", "x"],
        [b, "cleanup", "--", "wget"],
        [b, "pin", "--", "wget"],
        [b, "services", "restart", "--", "postgresql"],
        [b, "services", "list"],
        [b, "install", "--", "x"],
        [b, "outdated", "--json=v2", "--greedy"],
        ["brew", "outdated", "--json=v2"],  # not the pinned brew path
        ["launchctl", "bootout", "gui/501/com.morningbrew.run"],
        ["osascript", "-e", "x"],
        ["say", "hi"],
        ["fnox", "get", "--config", "/p/fnox.toml", "K"],
        ["ps", "-ax"],
        [],
    ):
        assert is_mutating(argv), argv


def test_recording_runner_never_executes_a_non_allowlisted_argv():
    class Inner:
        def __init__(self) -> None:
            self.ran: list[list[str]] = []

        def __call__(self, argv, timeout, input=None):
            self.ran.append(list(argv))
            return CommandResult(0, "", "")

    inner = Inner()
    rec = RecordingRunner(inner)  # type: ignore[arg-type]
    rec([_BREW, "upgrade", "--", "wget"], 1)
    rec(["fnox", "get", "K"], 1)
    rec([_BREW, "leaves"], 1)
    assert inner.ran == [[_BREW, "leaves"]]
    assert len(rec.mutating_calls) == 2


@pytest.mark.integration
def test_real_read_only_dry_run(real_deps, recording_runner):
    assert main(["run", "--dry-run"], deps_factory=lambda: real_deps) == 0
    assert recording_runner.calls, "the dry run issued no commands at all"
    assert recording_runner.mutating_calls == []
