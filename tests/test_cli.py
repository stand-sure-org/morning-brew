import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from fakes import FakeAudit, FakeHolds, FakeNotifier, FakeStamp, FakeStatus
from morning_brew import cli
from morning_brew.cli import CliDeps, main
from morning_brew.config import Config
from morning_brew.domain import (
    AuditEvent,
    Hold,
    HoldKind,
    RunResult,
    Stamp,
    Summary,
)
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
    ports.stamp = FakeStamp(calls=[])
    ports.notifier = FakeNotifier(calls=[])
    ports.clock = lambda: NOW
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
    assert fake_run.call_args.kwargs["dry_run"] is True
    assert callable(fake_run.call_args.kwargs["alarms"])
    assert fake_run.call_args.args == (deps.ports, deps.config)


def test_run_without_flag_is_not_dry_and_returns_exit_code(monkeypatch):
    fake_run = MagicMock(return_value=RunResult(exit_code=75, summary=None))
    monkeypatch.setattr(cli, "run_once", fake_run)
    factory, _, _ = make_deps()
    assert main(["run"], deps_factory=factory) == 75
    assert fake_run.call_args.kwargs["dry_run"] is False


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
    factory, deps, _ = make_deps(summary=make_summary())
    old = NOW - timedelta(hours=49)
    deps.ports.stamp.stamp = Stamp(old, old, "full", 3, old)
    assert main(["status"], deps_factory=factory) == 1


def test_status_recomputes_dead_man_from_the_stamp(capsys):
    # [I2] the summary said healthy, but 49 h have passed since the stamp
    factory, deps, _ = make_deps(summary=make_summary(dead_man=False))
    old = NOW - timedelta(hours=49)
    deps.ports.stamp.stamp = Stamp(old, old, "full", 3, old)
    assert main(["status"], deps_factory=factory) == 1
    assert "DEAD-MAN" in capsys.readouterr().out


def test_status_stale_summary_flag_is_not_trusted_over_fresh_stamp():
    factory, deps, _ = make_deps(summary=make_summary(dead_man=True))
    fresh = NOW - timedelta(hours=2)
    deps.ports.stamp.stamp = Stamp(fresh, fresh, "full", 3, fresh)
    assert main(["status"], deps_factory=factory) == 0


def test_status_without_summary_still_checks_the_stamp(capsys):
    factory, deps, _ = make_deps()
    first = NOW - timedelta(hours=49)
    deps.ports.stamp.stamp = Stamp(None, first, "degraded", 0, first)
    assert main(["status"], deps_factory=factory) == 1
    out = capsys.readouterr().out
    assert "no runs recorded" in out and "DEAD-MAN" in out


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


def test_explain_json_hold_kind_from_event_with_empty_hold_store(capsys):
    ev = event(stage="dry_run", verdict="hold", rule_id="R05", hold_kind="decided")
    main(["explain", "redis", "--json"], deps_factory=with_audit(ev))
    assert json.loads(capsys.readouterr().out)["hold_kind"] == "decided"


def test_explain_json_hold_kind_ignores_live_hold_store(capsys):
    holds = FakeHolds()
    holds.put(
        Hold(
            name="redis",
            kind=HoldKind.DECIDED,
            held_at_version="1",
            reason="x",
            first_held=NOW,
        )
    )
    ev = event(verdict="hold", rule_id="chain", hold_kind="transient")
    main(["explain", "redis", "--json"], deps_factory=with_audit(ev, holds=holds))
    assert json.loads(capsys.readouterr().out)["hold_kind"] == "transient"
    ev2 = event(verdict="eligible", rule_id="R12")
    main(["explain", "redis", "--json"], deps_factory=with_audit(ev2, holds=holds))
    assert json.loads(capsys.readouterr().out)["hold_kind"] is None


def test_explain_json_equal_ts_last_event_wins(capsys):
    first = event(run_id="r1", verdict="grey", rule_id="R11")
    final = event(run_id="r1", verdict="hold", rule_id="chain", hold_kind="decided")
    main(["explain", "redis", "--json"], deps_factory=with_audit(first, final))
    got = json.loads(capsys.readouterr().out)
    assert (got["decision"], got["rule_id"], got["hold_kind"]) == (
        "hold",
        "chain",
        "decided",
    )


def test_explain_json_equal_ts_across_runs_last_run_wins(capsys):
    a = event(run_id="rA", verdict="skip", rule_id="R02")
    b = event(run_id="rB", verdict="eligible", rule_id="R12")
    main(["explain", "redis", "--json"], deps_factory=with_audit(a, b))
    got = json.loads(capsys.readouterr().out)
    assert (got["run_id"], got["decision"]) == ("rB", "eligible")


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


# --- T24: wiring-facing CLI behaviour -------------------------------------------


def _raiser(exc: Exception):
    def fn() -> int:
        raise exc

    return fn


def capture_run(monkeypatch, summary: Summary) -> dict:
    """Fake run_once that applies the alarms hook the way the orchestrator does."""
    seen: dict = {}

    def fake(ports, config, *, dry_run=False, alarms=None):
        seen["dry_run"] = dry_run
        extra = tuple(alarms()) if alarms is not None else ()
        seen["alarms"] = extra
        return RunResult(
            exit_code=0, summary=replace(summary, anomalies=summary.anomalies + extra)
        )

    monkeypatch.setattr(cli, "run_once", fake)
    return seen


def test_run_surfaces_claude_auth_failure_as_urgent_anomaly(monkeypatch, capsys):
    seen = capture_run(monkeypatch, make_summary())
    factory, deps, _ = make_deps()
    deps.triage_error = lambda: "auth"
    assert main(["run"], deps_factory=factory) == 0
    assert any("auth" in a for a in seen["alarms"])
    # [M6] the orchestrator notifies once; the CLI never notifies again
    assert deps.ports.notifier.sent == []
    assert deps.ports.status.last is None
    assert "auth" in capsys.readouterr().out


def test_run_surfaces_claude_secret_failure(monkeypatch):
    seen = capture_run(monkeypatch, make_summary())
    factory, deps, _ = make_deps()
    deps.triage_error = lambda: "secret"
    main(["run"], deps_factory=factory)
    assert any("secret" in a for a in seen["alarms"])
    assert deps.ports.notifier.sent == []


def test_run_without_claude_error_does_not_renotify(monkeypatch):
    seen = capture_run(monkeypatch, make_summary())
    factory, deps, _ = make_deps()
    deps.triage_error = lambda: None
    main(["run"], deps_factory=factory)
    assert seen["alarms"] == ()
    assert deps.ports.notifier.sent == []


def test_dry_run_claude_error_is_printed_not_notified(monkeypatch, capsys):
    seen = capture_run(monkeypatch, make_summary())
    factory, deps, _ = make_deps()
    deps.triage_error = lambda: "auth"
    main(["run", "--dry-run"], deps_factory=factory)
    assert seen["dry_run"] is True
    assert deps.ports.notifier.sent == []
    assert deps.ports.status.last is None
    assert "auth" in capsys.readouterr().out


def test_run_auth_failure_notifies_exactly_once_end_to_end(monkeypatch):
    """[M6] real run_once: one urgent notification carrying the anomaly."""
    from builders import make_candidate, make_snapshot
    from fakes import (
        FakeBrew,
        FakeLive,
        FakeLock,
        FakeSeen,
        FakeTriage,
        grouping_components,
        recording_reconcile,
        recording_rules,
    )
    from morning_brew.domain import Decision, Verdict
    from morning_brew.ports import Ports

    calls: list[str] = []
    ports = Ports(
        brew=FakeBrew(calls, [make_snapshot(make_candidate("a"))]),
        live=FakeLive(),
        rules=recording_rules(calls, [Decision("a", Verdict.ELIGIBLE, "R12", "ok")]),
        components=grouping_components(calls, [["a"]]),
        reconcile=recording_reconcile(calls),
        triage=FakeTriage(calls, {}),
        holds=FakeHolds(),
        seen=FakeSeen(),
        audit=FakeAudit(calls),
        notifier=FakeNotifier(calls),
        status=FakeStatus(),
        lock=FakeLock(calls),
        stamp=FakeStamp(calls),
        clock=lambda: NOW,
    )
    deps = CliDeps(
        ports=ports,
        config=Config(),
        review=lambda: 0,
        install=lambda: 0,
        uninstall=lambda: 0,
        eval=lambda: 0,
        triage_error=lambda: "auth",
        gate_locked=True,
    )
    assert main(["run"], deps_factory=lambda: deps) == 0
    ((sent, urgent),) = ports.notifier.sent
    assert urgent is True
    assert any("auth" in a for a in sent.anomalies)
    assert cli.GATE_LOCKED_ANOMALY in sent.anomalies
    assert ports.status.last == sent


def test_run_gate_locked_adds_urgent_anomaly(monkeypatch):
    # [R56] live without a gate pass runs as shadow and says so, urgently
    seen = capture_run(monkeypatch, make_summary())
    factory, deps, _ = make_deps()
    deps.gate_locked = True
    main(["run"], deps_factory=factory)
    assert seen["alarms"] == ("live locked: gate not passed",)


def test_status_with_gate_locked_has_no_anomaly_and_works(capsys):
    factory, deps, _ = make_deps(summary=make_summary())
    deps.gate_locked = True
    assert main(["status"], deps_factory=factory) == 0
    assert "live locked" not in capsys.readouterr().out


def test_main_configures_logging_once(monkeypatch):
    import logging

    calls: list[dict] = []
    monkeypatch.setattr(cli, "_logging_configured", False)
    monkeypatch.setattr(logging, "basicConfig", lambda **kw: calls.append(kw))
    factory = make_deps()[0]
    main(["status"], deps_factory=factory)
    main(["status"], deps_factory=factory)
    assert calls == [
        {
            "level": logging.INFO,
            "format": "%(asctime)s %(levelname)s %(name)s: %(message)s",
        }
    ]


@pytest.mark.parametrize("cmd", ["review", "eval", "run"])
def test_brew_error_is_one_line_exit_1(cmd, monkeypatch, capsys):
    from morning_brew.domain import BrewParseError

    exc = BrewParseError("brew outdated --json=v2 exited 1: boom")
    monkeypatch.setattr(cli, "run_once", lambda *a, **k: (_ for _ in ()).throw(exc))
    factory, deps, _ = make_deps()
    deps.review = _raiser(exc)
    deps.eval = _raiser(exc)
    assert main([cmd], deps_factory=factory) == 1
    err = capsys.readouterr().err
    assert "boom" in err and "Traceback" not in err
    assert len(err.strip().splitlines()) == 1


def test_build_failure_gate_not_passed_is_clean_with_hint(capsys):
    from morning_brew.domain import GateNotPassed

    def factory():
        raise GateNotPassed("live chain mode requires a recorded passing eval")

    assert main(["run"], deps_factory=factory) == 1
    err = capsys.readouterr().err
    assert "shadow" in err and "morning-brew eval" in err
    assert len(err.strip().splitlines()) == 1


def test_eval_unlabeled_cases_message(capsys):
    from morning_brew.domain import UnlabeledCases

    factory, deps, _ = make_deps()
    deps.eval = _raiser(UnlabeledCases("r1, r2"))
    assert main(["eval"], deps_factory=factory) == 1
    assert (
        "label the 10 real caveats in golden-real-caveats.md first"
        in capsys.readouterr().err
    )


def test_status_shows_shadow_runs_in_shadow_mode(capsys):
    from morning_brew.shadow import ShadowReport

    factory, deps, _ = make_deps(summary=make_summary())
    deps.shadow = lambda: ShadowReport(3, 2, 1, 0)
    assert main(["status"], deps_factory=factory) == 0
    assert "shadow runs: 3/10" in capsys.readouterr().out


def test_status_shadow_line_with_no_runs(capsys):
    from morning_brew.shadow import ShadowReport

    factory, deps, _ = make_deps()
    deps.shadow = lambda: ShadowReport(0, 0, 0, 0)
    main(["status"], deps_factory=factory)
    out = capsys.readouterr().out
    assert "no runs recorded" in out and "shadow runs: 0/10" in out


def test_status_no_shadow_line_in_live_mode(capsys):
    from morning_brew.shadow import ShadowReport

    factory, deps, _ = make_deps(summary=make_summary())
    deps.config = Config(chain_mode="live")
    deps.shadow = lambda: ShadowReport(3, 2, 1, 0)
    main(["status"], deps_factory=factory)
    assert "shadow runs" not in capsys.readouterr().out


def test_setup_subcommand_dispatches(capsys):
    factory, deps, rec = make_deps()
    deps.setup = rec.make("setup", 0)
    assert main(["setup"], deps_factory=factory) == 0
    assert rec.calls == ["setup"]


def test_setup_unwired_is_a_clean_error(capsys):
    assert main(["setup"], deps_factory=make_deps()[0]) == 1
    assert "setup" in capsys.readouterr().err
