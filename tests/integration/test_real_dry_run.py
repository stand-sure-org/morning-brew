"""Real read-only dry run against this machine's brew (T24).

Deselected by default (``-m 'not integration'``). When run, every child
process goes through a recording wrapper around the real runner, and the test
asserts that no mutating argv was issued. State goes to a temporary directory
so the owner's real state is never touched; with fresh state everything is
first seen today, so the soak rule defers it and no model is called.
"""

import os
from collections.abc import Sequence
from pathlib import Path

import pytest

from morning_brew.brew.runner import SubprocessRunner
from morning_brew.cli import CliDeps, main
from morning_brew.config import child_env
from morning_brew.domain import CommandResult

_BREW_MUTATING = frozenset({"update", "upgrade", "cleanup", "pin", "unpin"})
_NEVER = frozenset({"launchctl"})
# Not mutating, but a read-only check must not notify or fetch the API key.
_BLOCKED = frozenset({"osascript", "say", "fnox"})


def is_mutating(argv: Sequence[str]) -> bool:
    if not argv:
        return False
    exe = Path(argv[0]).name
    if exe in _NEVER:
        return True
    if exe != "brew":
        return False
    args = list(argv[1:])
    if not args:
        return False
    sub = args[0]
    if sub == "upgrade":
        return "--dry-run" not in args
    if sub == "services":
        return len(args) > 1 and args[1] != "list"
    return sub in _BREW_MUTATING | {"install", "uninstall", "reinstall"}


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
        if is_mutating(argv) or Path(argv[0]).name in _BLOCKED:  # never executed
            return CommandResult(1, "", "blocked by test")
        return self._inner(argv, timeout, input=input)


@pytest.fixture
def recording_runner() -> RecordingRunner:
    return RecordingRunner(SubprocessRunner(child_env(os.environ)))


@pytest.fixture
def real_deps(tmp_path: Path, recording_runner: RecordingRunner) -> CliDeps:
    from morning_brew.wiring import build_deps

    return build_deps(None, state_dir=tmp_path / "state", runner=recording_runner)


def test_is_mutating_classifier():
    assert is_mutating(["/opt/homebrew/bin/brew", "update"])
    assert is_mutating(["/opt/homebrew/bin/brew", "upgrade", "--", "wget"])
    assert not is_mutating(
        ["/opt/homebrew/bin/brew", "upgrade", "--dry-run", "--", "x"]
    )
    assert is_mutating(["/opt/homebrew/bin/brew", "cleanup", "--", "wget"])
    assert not is_mutating(["/opt/homebrew/bin/brew", "outdated", "--json=v2"])
    assert not is_mutating(["/opt/homebrew/bin/brew", "services", "list", "--json"])
    assert not is_mutating(["ps", "-axo", "ppid=,comm="])


@pytest.mark.integration
def test_real_read_only_dry_run(real_deps, recording_runner):
    assert main(["run", "--dry-run"], deps_factory=lambda: real_deps) == 0
    assert recording_runner.calls, "the dry run issued no commands at all"
    assert recording_runner.mutating_calls == []
