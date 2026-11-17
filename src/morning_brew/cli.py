"""Command-line entry point. The only module allowed to print."""

import argparse
import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from morning_brew.config import Config
from morning_brew.domain import (
    EXIT_ERROR,
    EXIT_OK,
    AuditEvent,
    BrewBusyError,
    BrewParseError,
    BrewUpdateError,
    ConfigError,
    GateNotPassed,
    InstallError,
    InvalidPackageName,
    StateUnwritable,
    UnlabeledCases,
)
from morning_brew.orchestrator import run_once
from morning_brew.ports import Ports
from morning_brew.shadow import ShadowReport
from morning_brew.summary import render_summary
from morning_brew.textsafe import strip_control

EXIT_USAGE = 2
_DECISION_STAGES = frozenset({"decide", "dry_run"})
_NAME = re.compile(r"[A-Za-z0-9@._+/-]+")
_CLAUDE_ALARMS = frozenset({"auth", "secret"})
# [C16] Expected failures end in one clean line and exit 1, never a traceback.
_CLEAN_ERRORS = (
    BrewParseError,
    BrewBusyError,
    BrewUpdateError,
    InvalidPackageName,
    ConfigError,
    StateUnwritable,
    InstallError,
    OSError,
)
UNLABELED_MESSAGE = "label the 10 real caveats in golden-real-caveats.md first"
GATE_HINT = (
    'set chain_mode = "shadow", run `morning-brew eval` until it passes, '
    "then switch back to live"
)


@dataclass
class CliDeps:
    ports: Ports
    config: Config
    review: Callable[[], int]
    install: Callable[[], int]
    uninstall: Callable[[], int]
    eval: Callable[[], int]
    print_plist: Callable[[], bytes] | None = None
    # [C12] The run's Claude client error kind ("auth" / "secret" / None).
    triage_error: Callable[[], str | None] | None = None
    shadow: Callable[[], ShadowReport] | None = None


def _emit(text: str) -> None:
    print(strip_control(text))


def _err(text: str) -> None:
    print(strip_control(text), file=sys.stderr)


def _valid_name(name: str) -> bool:
    return bool(_NAME.fullmatch(name)) and not name.startswith("-") and ".." not in name


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="morning-brew")
    sub = p.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one triage-and-upgrade pass")
    run.add_argument("--dry-run", action="store_true")
    sub.add_parser("status", help="show the last run summary")
    ex = sub.add_parser("explain", help="show why a package was decided as it was")
    ex.add_argument("package")
    ex.add_argument("--json", action="store_true")
    sub.add_parser("review", help="review held packages")
    ins = sub.add_parser("install", help="install the launchd agent")
    ins.add_argument("--dry-run", action="store_true")
    sub.add_parser("uninstall", help="remove the launchd agent")
    sub.add_parser("eval", help="run the golden-set evaluation")
    return p


def _cmd_run(deps: CliDeps, dry_run: bool) -> int:
    result = run_once(deps.ports, deps.config, dry_run=dry_run)
    summary = result.summary
    kind = deps.triage_error() if deps.triage_error is not None else None
    if summary is not None and kind in _CLAUDE_ALARMS:
        # [C12] A stale or missing key would otherwise hold GREY members forever.
        anomaly = f"claude {kind} failure: check the API key (fnox)"
        summary = replace(summary, anomalies=(*summary.anomalies, anomaly))
        if not dry_run:  # a dry run records and notifies nothing
            deps.ports.status.write(summary)
            try:
                deps.ports.notifier.notify(summary, urgent=True)
            except Exception as exc:  # a broken notifier must never fail the run
                _err(f"notifier failed: {type(exc).__name__}")
    if summary is not None:
        _emit(render_summary(summary))
    return result.exit_code


def _shadow_line(deps: CliDeps) -> None:
    if deps.config.chain_mode != "shadow" or deps.shadow is None:
        return
    _emit(f"shadow runs: {deps.shadow().runs}/{deps.config.shadow_runs}")


def _cmd_status(deps: CliDeps) -> int:
    summary = deps.ports.status.read()
    if summary is None:
        _emit("no runs recorded")
        _shadow_line(deps)
        return EXIT_OK
    _emit(render_summary(summary))
    _shadow_line(deps)
    return EXIT_ERROR if summary.dead_man else EXIT_OK


def _latest_run_events(events: Sequence[AuditEvent]) -> list[AuditEvent]:
    """Decision events of the newest run; ties on ts go to the later-appended."""
    decided = [
        (e.ts, i, e)
        for i, e in enumerate(events)
        if e.stage in _DECISION_STAGES and e.verdict is not None
    ]
    if not decided:
        return []
    latest = max(decided, key=lambda t: (t[0], t[1]))[2]
    return [e for _, _, e in decided if e.run_id == latest.run_id]


def _cmd_explain(deps: CliDeps, package: str, as_json: bool) -> int:
    if not _valid_name(package):
        _err(f"invalid package name: {package}")
        return EXIT_USAGE
    events = deps.ports.audit.events_for(package)
    latest = _latest_run_events(events)
    if not latest:
        _err(f"no record for {package}")
        return EXIT_USAGE
    if as_json:
        final = latest[-1]  # events_for is append-ordered; the last one is final
        _emit(
            json.dumps(
                {
                    "package": package,
                    "decision": final.verdict,
                    "rule_id": final.rule_id,
                    "component": final.component,
                    "hold_kind": final.hold_kind,
                    "run_id": final.run_id,
                }
            )
        )
        return EXIT_OK
    _emit(f"Audit trail for {package}:")
    for e in sorted(events, key=lambda e: e.ts):
        parts = [e.ts.isoformat(), e.run_id, e.stage]
        parts += [p for p in (e.rule_id, e.verdict, e.action_result) if p]
        if e.component:
            parts.append(f"component={e.component}")
        _emit("  " + " ".join(parts))
        if e.reasoning:
            _emit(f"    reasoning: {e.reasoning}")
    return EXIT_OK


def _cmd_install(deps: CliDeps, dry_run: bool) -> int:
    if not dry_run:
        return deps.install()
    if deps.print_plist is None:
        _err("install --dry-run is unavailable: no plist renderer configured")
        return EXIT_ERROR
    _emit(deps.print_plist().decode("utf-8", errors="replace"))
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    deps_factory: Callable[[], CliDeps] | None = None,
) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    if deps_factory is None:
        from morning_brew.wiring import build_deps  # lazy: built in T24

        deps_factory = build_deps
    try:
        return _dispatch(args, deps_factory())
    except GateNotPassed as exc:
        _err(f"morning-brew: {_one_line(exc)}; {GATE_HINT}")
    except UnlabeledCases:
        _err(f"morning-brew eval: {UNLABELED_MESSAGE}")
    except _CLEAN_ERRORS as exc:
        _err(f"morning-brew {args.command}: {type(exc).__name__}: {_one_line(exc)}")
    return EXIT_ERROR


def _one_line(exc: BaseException) -> str:
    lines = str(exc).strip().splitlines()
    return lines[0] if lines else type(exc).__name__


def _dispatch(args: argparse.Namespace, deps: CliDeps) -> int:
    match args.command:
        case "run":
            return _cmd_run(deps, args.dry_run)
        case "status":
            return _cmd_status(deps)
        case "explain":
            return _cmd_explain(deps, args.package, args.json)
        case "review":
            return deps.review()
        case "install":
            return _cmd_install(deps, args.dry_run)
        case "uninstall":
            return deps.uninstall()
        case "eval":
            return deps.eval()
    _err(f"unknown command: {args.command}")  # pragma: no cover
    return EXIT_USAGE  # pragma: no cover
