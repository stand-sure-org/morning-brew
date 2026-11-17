"""Composition root: builds every real adapter and binds them into ``CliDeps``.

The only place that reads the process environment, ``sys.executable`` and
``sys.prefix``. Order matters: the state root is tightened to 0700 before any
store exists [C6], and the ship gate is checked before the triage chain is
built [C11], so live mode without a passing eval never constructs a chain.
"""

import dataclasses
import logging
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from morning_brew import launchd
from morning_brew.brew.adapter import BrewCli
from morning_brew.brew.runner import SubprocessRunner
from morning_brew.cli import CliDeps, _emit
from morning_brew.components import plan_components
from morning_brew.config import Config, child_env, detect_self_deps, load_config
from morning_brew.domain import EXIT_OK, CommandRunner
from morning_brew.liveprocess import LiveProcessScanner
from morning_brew.notify import (
    CommandNotifier,
    FanOutNotifier,
    NotificationNotifier,
    SayNotifier,
)
from morning_brew.ports import NotifierPort, Ports
from morning_brew.reconcile import reconcile
from morning_brew.review import review
from morning_brew.rules import evaluate_all
from morning_brew.secrets import fnox_get
from morning_brew.setup import read_trusted_taps
from morning_brew.shadow import ShadowReport, shadow_report
from morning_brew.store.audit import JsonlAudit
from morning_brew.store.holds import FileHoldStore
from morning_brew.store.lock import FileLock
from morning_brew.store.seen import FileSeenStore
from morning_brew.store.stamp import FileStampStore
from morning_brew.store.status import FileStatusStore

# The first-party local-model adapter, not the banned PyPI client package; one
# name per line so the witness's banned-dependency grep stays exact.
from morning_brew.triage import (
    ollama,
)
from morning_brew.triage.chain import TriageChain
from morning_brew.triage.claude import ClaudeClient
from morning_brew.triage.eval import (
    GateStore,
    eval_exit_code,
    load_cases,
    require_gate,
)

log = logging.getLogger(__name__)

APP = "morning-brew"


def default_state_dir() -> Path:
    return Path.home() / ".local" / "state" / APP


def default_config_path() -> Path:
    return Path.home() / ".config" / APP / "config.toml"


def default_fnox_config() -> Path:
    """[R19] ``<project root>/fnox.toml``: the directory holding ``.venv``.

    ``sys.prefix`` is the venv directory itself (no symlink resolution, which
    would land in the Homebrew interpreter). Outside a venv this is only a
    best guess, so it is logged.
    """
    if sys.prefix == sys.base_prefix:
        log.warning("not running in a virtualenv; fnox config guessed from prefix")
    return Path(sys.prefix).parent / "fnox.toml"


def venv_entry_point() -> Path:
    """The console script next to the venv interpreter (not resolved)."""
    return Path(sys.executable).parent / APP


def _now() -> datetime:
    return datetime.now().astimezone()  # [C4] tz-aware, local zone


def _run_id(kind: str, now: datetime) -> str:
    return f"{kind}-{now.astimezone(UTC).strftime('%Y%m%dT%H%M%SZ')}"


def _ensure_state_root(state_dir: Path) -> None:
    """[C6] Stores only guarantee their own files; the root is ours."""
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(state_dir, 0o700)


def _effective_config(config: Config, state_dir: Path) -> Config:
    return dataclasses.replace(
        config,
        # [C13] the owner's setup choice adds to, never replaces, the config
        trusted_taps=config.trusted_taps | read_trusted_taps(state_dir),
        fnox_config=config.fnox_config or default_fnox_config(),
    )


def _notifier(
    config: Config, runner: CommandRunner, clock: Callable[[], datetime]
) -> FanOutNotifier:
    urgent: list[NotifierPort] = [SayNotifier(runner)]
    if config.notify_command:
        urgent.append(CommandNotifier(config.notify_command, runner))
    return FanOutNotifier(
        normal=[NotificationNotifier(runner)],
        urgent=urgent,
        quiet_hours=config.quiet_hours,
        clock=clock,
        throttle_store_path=config.state_dir / "urgent_throttle.json",
    )


def build_deps(
    config_path: Path | None = None,
    *,
    state_dir: Path | None = None,
    runner: CommandRunner | None = None,
) -> CliDeps:
    state_dir = state_dir or default_state_dir()
    _ensure_state_root(state_dir)
    config = load_config(config_path or default_config_path(), state_dir=state_dir)
    config = _effective_config(config, state_dir)
    fnox_config = config.fnox_config
    assert fnox_config is not None  # set by _effective_config

    # [C7] one allowlisted-env runner for brew, notifiers, ps, lsof and fnox
    run = runner or SubprocessRunner(child_env(os.environ))
    clock = _now
    home = Path.home()

    gate = GateStore(state_dir / "gate.json")
    require_gate(config, gate)  # [C11] before any chain exists; raises

    local = (
        ollama.OllamaClient(config.local_model, config.local_digest, runner=run)
        if config.local_digest  # [R48] no pinned digest, no local stage
        else None
    )
    # [C12] exactly one client per run: its key and setup caches assume it
    claude = ClaudeClient(
        config.claude_model,
        config.claude_effort,
        key_provider=lambda: fnox_get(config.claude_key_name, run, fnox_config),
    )
    chain = TriageChain(local=local, claude=claude, mode=config.chain_mode)

    brew = BrewCli(run)
    live = LiveProcessScanner(run, home)
    audit = JsonlAudit(state_dir / "audit.jsonl")
    holds = FileHoldStore(state_dir)
    ports = Ports(
        brew=brew,
        live=live,
        rules=evaluate_all,
        components=plan_components,
        reconcile=reconcile,
        triage=chain,
        holds=holds,
        seen=FileSeenStore(state_dir / "seen.json"),
        audit=audit,
        notifier=_notifier(config, run, clock),
        status=FileStatusStore(state_dir / "status.json"),
        lock=FileLock(state_dir / "run.lock"),
        stamp=FileStampStore(state_dir / "stamp.json"),
        clock=clock,
        self_deps=detect_self_deps(Path(sys.executable)),  # [R13]
    )
    venv_bin = venv_entry_point()

    def do_review() -> int:
        now = clock()
        review(
            holds=holds,
            brew=brew,
            components=plan_components,
            live=live,
            audit=audit,
            ask=input,
            say=_emit,  # [C15] printing stays in cli.py
            now=now,
            run_id=_run_id("review", now),
            self_deps=ports.self_deps,
        )
        return EXIT_OK

    def do_eval() -> int:
        # [C8/C9] the gate is written by record_eval; exit from THAT report.
        now = clock()
        report = gate.record_eval(chain, audit, _run_id("eval", now), now)
        return eval_exit_code(report, load_cases())

    def do_install() -> int:
        launchd.install(run, venv_bin, home, os.getuid())
        return EXIT_OK

    def do_uninstall() -> int:
        launchd.uninstall(run, home, os.getuid())
        return EXIT_OK

    def do_shadow() -> ShadowReport:
        return shadow_report(audit.all_events())

    return CliDeps(
        ports=ports,
        config=config,
        review=do_review,
        install=do_install,
        uninstall=do_uninstall,
        eval=do_eval,
        print_plist=lambda: launchd.render_plist(venv_bin, home),
        triage_error=lambda: claude.last_error_kind,
        shadow=do_shadow,
    )
