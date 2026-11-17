"""Command-line entry point. The only module allowed to print."""

import argparse
import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from morning_brew.config import Config
from morning_brew.domain import EXIT_ERROR, EXIT_OK, AuditEvent
from morning_brew.orchestrator import run_once
from morning_brew.ports import Ports
from morning_brew.summary import render_summary
from morning_brew.textsafe import strip_control

EXIT_USAGE = 2
_DECISION_STAGES = frozenset({"decide", "dry_run"})
_NAME = re.compile(r"[A-Za-z0-9@._+/-]+")


@dataclass
class CliDeps:
    ports: Ports
    config: Config
    review: Callable[[], int]
    install: Callable[[], int]
    uninstall: Callable[[], int]
    eval: Callable[[], int]
    print_plist: Callable[[], bytes] | None = None


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
    if result.summary is not None:
        _emit(render_summary(result.summary))
    return result.exit_code


def _cmd_status(deps: CliDeps) -> int:
    summary = deps.ports.status.read()
    if summary is None:
        _emit("no runs recorded")
        return EXIT_OK
    _emit(render_summary(summary))
    return EXIT_ERROR if summary.dead_man else EXIT_OK


def _latest_run_events(events: Sequence[AuditEvent]) -> list[AuditEvent]:
    decided = [
        e for e in events if e.stage in _DECISION_STAGES and e.verdict is not None
    ]
    if not decided:
        return []
    latest = max(decided, key=lambda e: e.ts)
    return [e for e in decided if e.run_id == latest.run_id]


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
        final = max(latest, key=lambda e: e.ts)
        hold_kind = None
        if final.verdict == "hold":
            hold = deps.ports.holds.get(package)
            hold_kind = hold.kind.value if hold is not None else None
        _emit(
            json.dumps(
                {
                    "package": package,
                    "decision": final.verdict,
                    "rule_id": final.rule_id,
                    "component": final.component,
                    "hold_kind": hold_kind,
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
    deps = deps_factory()
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
