import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from fakes import FakeAudit, FakeHolds, FakeStatus
from morning_brew import cli
from morning_brew.cli import CliDeps, main
from morning_brew.config import Config
from morning_brew.domain import AuditEvent, Hold, HoldKind, RunResult, Summary
from morning_brew.summary import render_summary
from morning_brew.textsafe import strip_control

NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)


def make_summary(**overrides) -> Summary:
    base = Summary(
        run_id="r1",
        quality="full",
        upgraded=(),
        failed=(),
        decided_holds=0,
        oldest_decided_hold_days=None,
        blocked_by_dep=0,
        transient=0,
        self_dep_commands=(),
        disagreements=(),
        live_old_binary=(),
        anomalies=(),
        dead_man=False,
        degraded_reasons=(),
    )
    return replace(base, **overrides)


@dataclass
class Recorder:
    calls: list[str] = field(default_factory=list)

    def make(self, name: str, rc: int = 0):
        def fn() -> int:
            self.calls.append(name)
            return rc

        return fn


def make_deps(
    *,
    summary: Summary | None = None,
    events: list[AuditEvent] | None = None,
    holds: FakeHolds | None = None,
    rec: Recorder | None = None,
    print_plist=None,
):
    rec = rec or Recorder()
    ports = MagicMock()
    ports.status = FakeStatus(last=summary)
    ports.audit = FakeAudit(calls=[], events=list(events or []))
    ports.holds = holds or FakeHolds()
    deps = CliDeps(
        ports=ports,
        config=Config(),
        review=rec.make("review", 3),
        install=rec.make("install", 4),
        uninstall=rec.make("uninstall", 5),
        eval=rec.make("eval", 6),
        print_plist=print_plist,
    )
    return (lambda: deps), deps, rec


def with_summary(**overrides):
    return make_deps(summary=make_summary(**overrides))[0]


def with_audit(*events: AuditEvent, holds: FakeHolds | None = None):
    return make_deps(events=list(events), holds=holds)[0]


def event(pkg="redis", run_id="r1", ts=NOW, **kw) -> AuditEvent:
    base = dict(
        run_id=run_id,
        ts=ts,
        brew_version="7.0.14",
        stage="decide",
        package=pkg,
        rule_id="R02",
        verdict="skip",
        action_result="skipped",
    )
    base.update(kw)
    return AuditEvent(**base)


def test_run_dry_run_flag_reaches_orchestrator(monkeypatch):
    fake_run = MagicMock(return_value=RunResult(exit_code=0, summary=None))
    monkeypatch.setattr(cli, "run_once", fake_run)
    factory, deps, _ = make_deps()
    assert main(["run", "--dry-run"], deps_factory=factory) == 0
    assert fake_run.call_args.kwargs == {"dry_run": True}
    assert fake_run.call_args.args == (deps.ports, deps.config)


def test_run_without_flag_is_not_dry_and_returns_exit_code(monkeypatch):
    fake_run = MagicMock(return_value=RunResult(exit_code=75, summary=None))
    monkeypatch.setattr(cli, "run_once", fake_run)
    factory, _, _ = make_deps()
    assert main(["run"], deps_factory=factory) == 75
    assert fake_run.call_args.kwargs == {"dry_run": False}


def test_run_prints_sanitized_summary(monkeypatch, capsys):
    s = make_summary(upgraded=("wget\x1b[31m",))
    monkeypatch.setattr(
        cli, "run_once", lambda *a, **k: RunResult(exit_code=0, summary=s)
    )
    assert main(["run"], deps_factory=make_deps()[0]) == 0
    out = capsys.readouterr().out
    assert "wget" in out and "\x1b" not in out


def test_status_prints_copyable_self_dep_command(capsys):
    main(
        ["status"],
        deps_factory=with_summary(self_dep_commands=("brew upgrade -- fnox usage",)),
    )
    assert "brew upgrade -- fnox usage" in capsys.readouterr().out


def test_status_dead_man_exit_code():
    assert main(["status"], deps_factory=with_summary(dead_man=True)) == 1


def test_status_healthy_exit_code():
    assert main(["status"], deps_factory=with_summary()) == 0


def test_status_no_summary(capsys):
    assert main(["status"], deps_factory=make_deps()[0]) == 0
    assert "no runs recorded" in capsys.readouterr().out


def test_output_strips_ansi_and_control(capsys):
    main(
        ["status"],
        deps_factory=with_summary(disagreements=("grype \x1b[31mEVIL\x1b[0m\x07",)),
    )
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out and "EVIL" in out


def test_status_shows_local_flagged_claude_cleared(capsys):
    main(["status"], deps_factory=with_summary(disagreements=("grype", "syft")))
    out = capsys.readouterr().out
    label_at = out.index("Local flagged, Claude cleared")
    assert "grype" in out[label_at:] and "syft" in out[label_at:]


def test_strip_control_unit():
    assert strip_control("a\x1b[1;31mb\x1b[0mc") == "abc"
    assert strip_control("a\x1b]0;title\x07b") == "ab"
    assert strip_control("a\x00b\x07c\x7fd\x85e\x80f") == "abcdef"
    assert strip_control("l1\nl2\tx\r\n") == "l1\nl2\tx\n"
    assert strip_control("héllo ✓") == "héllo ✓"


def test_render_summary_sections():
    text = render_summary(
        make_summary(
            quality="degraded",
            degraded_reasons=("ollama down",),
            upgraded=("wget",),
            failed=("jq",),
            decided_holds=2,
            oldest_decided_hold_days=5,
            blocked_by_dep=1,
            transient=3,
            live_old_binary=("redis",),
            anomalies=("odd",),
            dead_man=True,
        )
    )
    for needle in (
        "r1",
        "degraded",
        "ollama down",
        "wget",
        "jq",
        "5",
        "redis",
        "odd",
        "DEAD-MAN",
    ):
        assert needle in text


def test_explain_lists_audit_trail(capsys):
    older = event(run_id="r0", ts=NOW.replace(hour=1), verdict="hold", rule_id="R05")
    newer = event(reasoning="because")
    rc = main(["explain", "redis"], deps_factory=with_audit(older, newer))
    out = capsys.readouterr().out
    assert rc == 0
    assert "r0" in out and "r1" in out and "R05" in out and "R02" in out
    assert "because" in out


def test_explain_unknown_package_exits_2_plain(capsys):
    assert main(["explain", "nope"], deps_factory=with_audit()) == 2


def test_explain_json_shape(capsys):
    main(["explain", "redis", "--json"], deps_factory=with_audit(event()))
    assert json.loads(capsys.readouterr().out) == {
        "package": "redis",
        "decision": "skip",
        "rule_id": "R02",
        "component": None,
        "hold_kind": None,
        "run_id": "r1",
    }


def test_explain_json_uses_latest_run_dry_run_included(capsys):
    old = event(run_id="r1", ts=NOW.replace(hour=1), verdict="eligible", rule_id="R01")
    new = event(
        run_id="r2",
        ts=NOW,
        stage="dry_run",
        verdict="hold",
        rule_id="chain",
        component="a,redis",
    )
    stage_noise = event(run_id="r3", ts=NOW.replace(hour=12), stage="notify")
    main(["explain", "redis", "--json"], deps_factory=with_audit(old, new, stage_noise))
    got = json.loads(capsys.readouterr().out)
    assert got["run_id"] == "r2"
    assert got["decision"] == "hold"
    assert got["rule_id"] == "chain"
    assert got["component"] == "a,redis"


def test_explain_json_hold_kind_from_hold_store(capsys):
    holds = FakeHolds()
    holds.put(
        Hold(
            name="redis",
            kind=HoldKind.TRANSIENT,
            held_at_version="1",
            reason="x",
            first_held=NOW,
        )
    )
    ev = event(verdict="hold", rule_id="chain")
    main(["explain", "redis", "--json"], deps_factory=with_audit(ev, holds=holds))
    assert json.loads(capsys.readouterr().out)["hold_kind"] == "transient"


def test_explain_json_unknown_package_exits_2(capsys):
    assert main(["explain", "nope", "--json"], deps_factory=with_audit()) == 2
    assert capsys.readouterr().out == ""


def test_explain_rejects_invalid_package_name(capsys):
    assert main(["explain", "-bad"], deps_factory=with_audit()) == 2


def test_install_dry_run_prints_plist_and_installs_nothing(capsys):
    factory, _, rec = make_deps(print_plist=lambda: b"<plist>StartInterval</plist>")
    assert main(["install", "--dry-run"], deps_factory=factory) == 0
    assert "StartInterval" in capsys.readouterr().out
    assert rec.calls == []


def test_install_dry_run_without_plist_callable_errors(capsys):
    factory, _, rec = make_deps()
    assert main(["install", "--dry-run"], deps_factory=factory) == 1
    assert rec.calls == []


@pytest.mark.parametrize(
    "cmd,rc", [("review", 3), ("install", 4), ("uninstall", 5), ("eval", 6)]
)
def test_dispatch(cmd, rc):
    factory, _, rec = make_deps()
    assert main([cmd], deps_factory=factory) == rc
    assert rec.calls == [cmd]


def test_no_subcommand_exits_2():
    assert main([], deps_factory=make_deps()[0]) == 2
