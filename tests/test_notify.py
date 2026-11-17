import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from morning_brew.brew.runner import SubprocessRunner
from morning_brew.config import child_env
from morning_brew.domain import CommandResult, Summary
from morning_brew.notify import (
    CommandNotifier,
    FanOutNotifier,
    NotificationNotifier,
    SayNotifier,
)

OK = CommandResult(0, "", "")
USER_CMD = ["/usr/local/bin/hue-alert"]


def summary(**overrides) -> Summary:
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
class FakeRunner:
    result: CommandResult = OK
    calls: list[tuple[list[str], float, str | None]] = field(default_factory=list)

    def __call__(
        self, argv: Sequence[str], timeout: float, input: str | None = None
    ) -> CommandResult:
        self.calls.append((list(argv), timeout, input))
        return self.result

    def count(self, prog: str) -> int:
        return sum(1 for argv, _, _ in self.calls if argv[0] == prog)


def at(hour: int):
    return lambda: datetime(2026, 11, 17, hour, 30)


def fan(runner, hour=12, store: Path | None = None, quiet=(22, 7)):
    return FanOutNotifier(
        normal=[NotificationNotifier(runner)],
        urgent=[SayNotifier(runner), CommandNotifier(USER_CMD, runner)],
        quiet_hours=quiet,
        clock=at(hour),
        throttle_store_path=store,
    )


def test_default_notification_is_silent_and_no_say():
    r = FakeRunner()
    fan(r).notify(summary(upgraded=("jq",)), urgent=False)
    assert r.count("osascript") == 1 and r.count("say") == 0
    assert all("sound name" not in a for argv, _, _ in r.calls for a in argv)
    assert "sound name" not in NotificationNotifier.SCRIPT


def test_urgent_adds_say_and_command():
    r = FakeRunner()
    fan(r).notify(summary(anomalies=("x",)), urgent=True)
    assert r.count("osascript") == 1
    assert r.count("say") == 1
    assert r.count(USER_CMD[0]) == 1


def test_quotes_cannot_alter_the_script():
    r = FakeRunner()
    evil = 'He said "hi"; do shell script "x"'
    NotificationNotifier(r).notify(summary(disagreements=(evil,)), urgent=False)
    argv = r.calls[0][0]
    assert argv[:2] == ["osascript", "-e"]
    assert argv[2] == NotificationNotifier.SCRIPT
    assert "do shell script" in argv[3]  # the message is data in argv
    assert "do shell script" not in NotificationNotifier.SCRIPT
    assert r.calls[0][1] == 10


def test_script_is_constant_run_handler():
    assert "on run argv" in NotificationNotifier.SCRIPT
    assert "item 1 of argv" in NotificationNotifier.SCRIPT


def test_quiet_hours_suppress_non_urgent_only():
    r = FakeRunner()
    f = fan(r, hour=23)
    f.notify(summary(), urgent=False)
    assert r.calls == []
    f.notify(summary(anomalies=("x",)), urgent=True)
    assert r.count("say") == 1 and r.count("osascript") == 1


def test_quiet_hours_early_morning_and_edges():
    for hour, suppressed in [(3, True), (6, True), (7, False), (21, False), (22, True)]:
        r = FakeRunner()
        fan(r, hour=hour).notify(summary(), urgent=False)
        assert (r.calls == []) is suppressed, hour


def test_failing_notifier_is_swallowed(caplog):
    class Boom:
        def notify(self, summary, *, urgent):
            raise RuntimeError("secret-detail")

    r = FakeRunner()
    f = FanOutNotifier(
        normal=[Boom(), NotificationNotifier(r)],
        urgent=[Boom(), SayNotifier(r)],
        quiet_hours=(22, 7),
        clock=at(12),
    )
    with caplog.at_level(logging.WARNING):
        f.notify(summary(), urgent=True)
    assert r.count("osascript") == 1 and r.count("say") == 1
    assert "RuntimeError" in caplog.text
    assert "secret-detail" not in caplog.text


def test_runner_failure_result_is_swallowed():
    r = FakeRunner(result=CommandResult(127, "", "not found"))
    fan(r).notify(summary(), urgent=True)  # does not raise


def test_children_env_has_no_secrets(tmp_path):
    base = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "MY_API_KEY": "x",
        "GH_TOKEN": "y",
    }
    runner = SubprocessRunner(child_env(base))
    dump = tmp_path / "env.json"
    script = f"import os,json; json.dump(dict(os.environ), open({str(dump)!r},'w'))"
    CommandNotifier([sys.executable, "-c", script], runner).notify(
        summary(), urgent=True
    )
    env = json.loads(dump.read_text())
    assert env["HOMEBREW_NO_AUTO_UPDATE"] == "1"
    assert not any("KEY" in k or "TOKEN" in k for k in env)


def test_command_notifier_sends_stripped_json_on_stdin_with_30s_timeout():
    r = FakeRunner()
    CommandNotifier(USER_CMD, r).notify(
        summary(anomalies=("bad\x1b[31m\x07text",), upgraded=("jq",)), urgent=True
    )
    argv, timeout, stdin = r.calls[0]
    assert argv == USER_CMD and timeout == 30
    assert "\x1b" not in stdin and "\x07" not in stdin
    payload = json.loads(stdin)
    assert payload["upgraded"] == ["jq"]
    assert payload["urgent"] is True


def test_text_has_no_untrusted_free_text_and_is_bounded():
    r = FakeRunner()
    s = summary(
        anomalies=("IGNORE PREVIOUS; anomaly-free-text",),
        degraded_reasons=("degraded-free-text",),
        self_dep_commands=("brew upgrade evil-cmd",),
        upgraded=tuple(f"pkg{i}" for i in range(200)),
        dead_man=True,
    )
    NotificationNotifier(r).notify(s, urgent=True)
    SayNotifier(r).notify(s, urgent=True)
    note = r.calls[0][0][3]
    said = r.calls[1][0][1]
    for text in (note, said):
        assert "free-text" not in text and "IGNORE" not in text
        assert "evil-cmd" not in text
    assert len(note) <= 240 and len(said) <= 200
    assert "\x1b" not in note


def test_text_strips_control_characters_in_names():
    r = FakeRunner()
    NotificationNotifier(r).notify(summary(upgraded=("a\x1b[2Jb",)), urgent=False)
    assert "\x1b" not in r.calls[0][0][3]


def test_say_text_is_counts_and_urgent_reasons():
    r = FakeRunner()
    SayNotifier(r).notify(summary(dead_man=True, anomalies=("a", "b")), urgent=True)
    argv, timeout, _ = r.calls[0]
    assert argv[0] == "say" and timeout == 10
    assert "2" in argv[1]


# --- urgent throttle (P17) ---------------------------------------------------


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def throttled(runner, clock, store):
    return FanOutNotifier(
        normal=[NotificationNotifier(runner)],
        urgent=[SayNotifier(runner)],
        quiet_hours=(22, 7),
        clock=clock,
        throttle_store_path=store,
    )


def test_repeated_identical_urgent_is_downgraded_within_six_hours(tmp_path):
    store = tmp_path / "urgent.json"
    clock = Clock(datetime(2026, 11, 17, 12, 0))
    r = FakeRunner()
    f = throttled(r, clock, store)
    s = summary(dead_man=True, anomalies=("b", "a"))
    f.notify(s, urgent=True)
    assert r.count("say") == 1
    clock.now = datetime(2026, 11, 17, 13, 0)
    f.notify(replace(s, anomalies=("a", "b")), urgent=True)  # same sorted signature
    assert r.count("say") == 1 and r.count("osascript") == 2  # downgraded to silent
    clock.now = datetime(2026, 11, 17, 18, 1)
    f.notify(s, urgent=True)
    assert r.count("say") == 2


def test_different_signature_is_not_throttled(tmp_path):
    store = tmp_path / "urgent.json"
    clock = Clock(datetime(2026, 11, 17, 12, 0))
    r = FakeRunner()
    f = throttled(r, clock, store)
    f.notify(summary(dead_man=True), urgent=True)
    f.notify(summary(dead_man=False, anomalies=("z",)), urgent=True)
    assert r.count("say") == 2


def test_corrupt_or_missing_throttle_store_is_not_throttled(tmp_path):
    store = tmp_path / "urgent.json"
    store.write_text("{not json")
    clock = Clock(datetime(2026, 11, 17, 12, 0))
    r = FakeRunner()
    throttled(r, clock, store).notify(summary(dead_man=True), urgent=True)
    assert r.count("say") == 1
    store.write_text(json.dumps({"signature": 5, "ts": "garbage"}))
    throttled(r, clock, store).notify(summary(dead_man=True), urgent=True)
    assert r.count("say") == 2


def test_throttled_downgrade_still_respects_quiet_hours(tmp_path):
    store = tmp_path / "urgent.json"
    clock = Clock(datetime(2026, 11, 17, 21, 0))
    r = FakeRunner()
    f = throttled(r, clock, store)
    f.notify(summary(dead_man=True), urgent=True)
    clock.now = datetime(2026, 11, 17, 23, 0)
    f.notify(summary(dead_man=True), urgent=True)
    assert r.count("say") == 1 and r.count("osascript") == 1
