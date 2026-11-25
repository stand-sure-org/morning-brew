import logging
import plistlib
from pathlib import Path

import pytest

from builders import make_candidate, make_snapshot
from morning_brew.domain import CommandResult, Snapshot
from morning_brew.liveprocess import (
    LiveProcessScanner,
    compute_live_set,
    discover_launch_plists,
    list_running_executables,
)


@pytest.fixture
def prefix(tmp_path: Path) -> Path:
    p = (tmp_path / "homebrew").resolve()
    (p / "Cellar").mkdir(parents=True)
    (p / "opt").mkdir()
    (p / "bin").mkdir()
    return p


def make_keg(prefix: Path, name: str, version: str, exe: str = "") -> Path:
    keg = prefix / "Cellar" / name / version
    exe_path = keg / (exe or f"bin/{name}")
    exe_path.parent.mkdir(parents=True, exist_ok=True)
    exe_path.write_text("")
    (prefix / "opt" / name).symlink_to(keg)
    return exe_path


def write_plist(path: Path, args: list[str]) -> Path:
    path.write_bytes(plistlib.dumps({"Label": "x", "ProgramArguments": args}))
    return path


def snap(services: dict[str, str] | None = None, **deps: tuple[str, ...]) -> Snapshot:
    names = {"vectordotdev/brew/vector", "rabbitmq", "erlang", "caddy", "nats-server"}
    s = make_snapshot(*(make_candidate(n) for n in sorted(names)))
    return make_snapshot(
        *s.candidates,
        services=services or {},
        dep_graph={**dict(s.dep_graph), **deps},
    )


def test_vector_live_via_user_launchagent_even_when_services_say_none(
    tmp_path: Path, prefix: Path
):
    make_keg(prefix, "vector", "0.61.0")
    plist = write_plist(tmp_path / "a.plist", [str(prefix / "opt/vector/bin/vector")])
    s = snap({"vectordotdev/brew/vector": "none"})
    live = compute_live_set(s, launch_plists=[plist], running_exes=[], prefix=prefix)
    assert "vectordotdev/brew/vector" in live


def test_launchagent_via_bin_symlink(tmp_path: Path, prefix: Path):
    make_keg(prefix, "nats-server", "2.10.0")
    (prefix / "bin" / "nats-server").symlink_to(
        "../Cellar/nats-server/2.10.0/bin/nats-server"
    )
    plist = write_plist(tmp_path / "a.plist", [str(prefix / "bin/nats-server")])
    live = compute_live_set(
        snap(), launch_plists=[plist], running_exes=[], prefix=prefix
    )
    assert "nats-server" in live


def test_started_service_pulls_in_dependency_closure(prefix: Path):
    s = snap({"rabbitmq": "started"}, rabbitmq=("erlang",))
    live = compute_live_set(s, launch_plists=[], running_exes=[], prefix=prefix)
    assert {"rabbitmq", "erlang"} <= live


def test_closure_is_transitive(prefix: Path):
    s = snap({"rabbitmq": "scheduled"}, rabbitmq=("caddy",), caddy=("erlang",))
    live = compute_live_set(s, launch_plists=[], running_exes=[], prefix=prefix)
    assert {"rabbitmq", "caddy", "erlang"} <= live


def test_running_exe_from_versioned_keg(prefix: Path):
    exe = make_keg(prefix, "erlang", "29.1.2", "lib/erlang/erts/bin/beam.smp")
    live = compute_live_set(snap(), launch_plists=[], running_exes=[exe], prefix=prefix)
    assert "erlang" in live


def test_service_block_with_nothing_live_is_not_in_set(prefix: Path):
    s = snap({"caddy": "none"})
    live = compute_live_set(s, launch_plists=[], running_exes=[], prefix=prefix)
    assert "caddy" not in live


def test_symlink_is_followed(prefix: Path):
    make_keg(prefix, "erlang", "29.1.2")
    live = compute_live_set(
        snap(),
        launch_plists=[],
        running_exes=[prefix / "opt/erlang/bin/erlang"],
        prefix=prefix,
    )
    assert "erlang" in live


def test_path_outside_prefix_is_ignored(tmp_path: Path, prefix: Path):
    other = tmp_path / "elsewhere/Cellar/erlang/1/bin/erl"
    other.parent.mkdir(parents=True)
    other.write_text("")
    live = compute_live_set(
        snap(), launch_plists=[], running_exes=[other], prefix=prefix
    )
    assert live == frozenset()


def test_uninstalled_formula_is_ignored(prefix: Path):
    exe = make_keg(prefix, "ghost", "1.0")
    live = compute_live_set(snap(), launch_plists=[], running_exes=[exe], prefix=prefix)
    assert live == frozenset()


def test_malformed_plist_is_skipped(tmp_path: Path, prefix: Path, caplog):
    bad = tmp_path / "bad.plist"
    bad.write_text("not a plist")
    with caplog.at_level(logging.WARNING):
        live = compute_live_set(
            snap(), launch_plists=[bad], running_exes=[], prefix=prefix
        )
    assert live == frozenset()
    assert "bad.plist" in caplog.text


def test_unreadable_plist_is_skipped(tmp_path: Path, prefix: Path, caplog):
    with caplog.at_level(logging.WARNING):
        compute_live_set(
            snap(),
            launch_plists=[tmp_path / "missing.plist"],
            running_exes=[],
            prefix=prefix,
        )
    assert "missing.plist" in caplog.text


def test_plist_without_program_arguments_is_skipped(tmp_path: Path, prefix: Path):
    p = tmp_path / "n.plist"
    p.write_bytes(plistlib.dumps({"Label": "x"}))
    assert (
        compute_live_set(snap(), launch_plists=[p], running_exes=[], prefix=prefix)
        == frozenset()
    )


def test_discover_launch_plists_lists_user_agents(tmp_path: Path):
    agents = tmp_path / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    (agents / "a.plist").write_text("")
    (agents / "notes.txt").write_text("")
    found = discover_launch_plists(tmp_path)
    assert agents / "a.plist" in found
    assert agents / "notes.txt" not in found


def test_discover_launch_plists_tolerates_missing_dirs(tmp_path: Path):
    assert isinstance(discover_launch_plists(tmp_path), list)


def ps_runner(stdout: str, returncode: int = 0):
    calls: list[tuple[list[str], float]] = []

    def run(argv, timeout):
        calls.append((list(argv), timeout))
        return CommandResult(returncode, stdout, "")

    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_launchd_child_is_live():
    runner = ps_runner("    1 /opt/homebrew/opt/etcd/bin/etcd\n")
    assert list_running_executables(runner) == [Path("/opt/homebrew/opt/etcd/bin/etcd")]
    assert runner.calls[0][0] == ["ps", "-axo", "ppid=,comm="]  # type: ignore[attr-defined]


def test_interactive_process_is_not_live():
    runner = ps_runner(
        "  501 /opt/homebrew/bin/bash\n    1 /opt/homebrew/opt/loki/bin/loki\n"
    )
    assert list_running_executables(runner) == [Path("/opt/homebrew/opt/loki/bin/loki")]


def test_command_with_spaces_and_junk_lines():
    runner = ps_runner("garbage\n    1 /Applications/Some App/bin x\n\n")
    assert list_running_executables(runner) == [Path("/Applications/Some App/bin x")]


def test_ps_failure_returns_empty():
    assert list_running_executables(ps_runner("", returncode=1)) == []


def test_scanner_composes_all_sources(tmp_path: Path, prefix: Path):
    make_keg(prefix, "vector", "0.61.0")
    make_keg(prefix, "erlang", "29.1.2")
    agents = tmp_path / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    write_plist(agents / "v.plist", [str(prefix / "opt/vector/bin/vector")])
    runner = ps_runner(f"    1 {prefix}/Cellar/erlang/29.1.2/bin/erlang\n")
    scanner = LiveProcessScanner(runner, tmp_path, prefix)
    live = scanner.live_set(snap({"caddy": "started"}))
    assert {"vectordotdev/brew/vector", "erlang", "caddy"} <= live
    assert runner.calls[0][1] > 0  # type: ignore[attr-defined]
