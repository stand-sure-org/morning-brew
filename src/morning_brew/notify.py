"""Notifiers (FR-071): silent by default, loud only for urgent events.

Notification and ``say`` text is built from counts and validated package names
only; hold reasons, model reasoning, anomaly and degraded text are untrusted-
derived and never reach it. Every notifier failure is swallowed.
"""

import dataclasses
import json
import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from pathlib import Path

from morning_brew.domain import CommandRunner, Summary
from morning_brew.ports import NotifierPort
from morning_brew.store.atomic import read_json_or_none, write_json_atomic
from morning_brew.textsafe import strip_control

log = logging.getLogger(__name__)

TITLE = "brew-triage"
NOTIFICATION_MAX = 240
SAY_MAX = 200
TIMEOUT = 10
COMMAND_TIMEOUT = 30
URGENT_THROTTLE = timedelta(hours=6)
_NAME_LIST_MAX = 5


def _clip(text: str, limit: int) -> str:
    return strip_control(text).replace("\n", " ")[:limit]


def _names(label: str, names: Sequence[str]) -> str | None:
    if not names:
        return None
    shown = ", ".join(names[:_NAME_LIST_MAX])
    more = f" +{len(names) - _NAME_LIST_MAX}" if len(names) > _NAME_LIST_MAX else ""
    return f"{len(names)} {label} ({shown}{more})"


def _notification_text(summary: Summary) -> str:
    parts: list[str | None] = []
    if summary.dead_man:
        parts.append("DEAD-MAN alarm")
    if summary.quality == "degraded":
        parts.append("degraded run")
    parts.append(_names("upgraded", summary.upgraded))
    parts.append(_names("failed", summary.failed))
    parts.append(_names("flagged-vs-cleared", summary.disagreements))
    parts.append(_names("need restart", summary.live_old_binary))
    for count, label in (
        (summary.decided_holds, "held"),
        (summary.blocked_by_dep, "blocked by dependency"),
        (summary.transient, "transient"),
        (len(summary.self_dep_commands), "need your command"),
        (len(summary.anomalies), "anomalies"),
    ):
        if count:
            parts.append(f"{count} {label}")
    text = "; ".join(p for p in parts if p) or "nothing to do"
    return _clip(text, NOTIFICATION_MAX)


def _say_text(summary: Summary) -> str:
    parts = ["Brew triage needs attention."]
    if summary.dead_man:
        parts.append("Dead man alarm.")
    if summary.anomalies:
        parts.append(f"{len(summary.anomalies)} anomalies.")
    if summary.failed:
        parts.append(f"{len(summary.failed)} failed.")
    return _clip(" ".join(parts), SAY_MAX)


class NotificationNotifier:
    """macOS notification via osascript; the message is an argv element."""

    # Constant script: untrusted text is only ever an argv item, never source.
    SCRIPT = (
        "on run argv\n"
        "display notification (item 1 of argv) with title (item 2 of argv)\n"
        "end run"
    )

    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    def notify(self, summary: Summary, *, urgent: bool) -> None:
        argv = ["osascript", "-e", self.SCRIPT, _notification_text(summary), TITLE]
        self._runner(argv, TIMEOUT)

    def notify_text(self, text: str) -> None:
        """[R57] Free text built by the tool (the digest); still clipped."""
        argv = ["osascript", "-e", self.SCRIPT, _clip(text, NOTIFICATION_MAX), TITLE]
        self._runner(argv, TIMEOUT)


class SayNotifier:
    def __init__(self, runner: CommandRunner) -> None:
        self._runner = runner

    def notify(self, summary: Summary, *, urgent: bool) -> None:
        self._runner(["say", _say_text(summary)], TIMEOUT)


class CommandNotifier:
    """Runs the owner's own script with the summary as JSON on stdin."""

    def __init__(self, argv: Sequence[str], runner: CommandRunner) -> None:
        self._argv = list(argv)
        self._runner = runner

    def notify(self, summary: Summary, *, urgent: bool) -> None:
        payload = {**dataclasses.asdict(summary), "urgent": urgent}
        body = strip_control(json.dumps(payload, ensure_ascii=True))
        self._runner(self._argv, COMMAND_TIMEOUT, input=body)


class FanOutNotifier:
    def __init__(
        self,
        normal: Sequence[NotifierPort],
        urgent: Sequence[NotifierPort],
        quiet_hours: tuple[int, int],
        clock: Callable[[], datetime],
        throttle_store_path: Path | None = None,
    ) -> None:
        self._normal = list(normal)
        self._urgent = list(urgent)
        self._quiet = quiet_hours
        self._clock = clock
        self._store = throttle_store_path

    def notify(self, summary: Summary, *, urgent: bool) -> None:
        now = self._clock()
        signature = _signature(summary)
        if urgent and self._throttled(signature, now):
            urgent = False
        if not urgent and self._is_quiet(now):
            return
        for notifier in self._normal:
            self._safely(notifier, summary, urgent)
        if urgent:
            for notifier in self._urgent:
                self._safely(notifier, summary, urgent)
            self._record(signature, now)

    def notify_text(self, text: str) -> bool:
        """[R57] Normal channel only, silenced by quiet hours.

        True only when at least one normal notifier delivered it, so a caller
        can retry what quiet hours or a broken notifier swallowed.
        """
        if self._is_quiet(self._clock()):
            return False
        delivered = False
        for notifier in self._normal:
            send = getattr(notifier, "notify_text", None)
            if send is None:
                continue
            try:
                send(text)
            except Exception as exc:
                log.warning(
                    "notifier %s failed: %s",
                    type(notifier).__name__,
                    type(exc).__name__,
                )
                continue
            delivered = True
        return delivered

    def _is_quiet(self, now: datetime) -> bool:
        start, end = self._quiet
        if start > end:
            return now.hour >= start or now.hour < end
        return start <= now.hour < end

    @staticmethod
    def _safely(notifier: NotifierPort, summary: Summary, urgent: bool) -> None:
        try:
            notifier.notify(summary, urgent=urgent)
        except Exception as exc:
            log.warning(
                "notifier %s failed: %s", type(notifier).__name__, type(exc).__name__
            )

    def _throttled(self, signature: str, now: datetime) -> bool:
        if self._store is None:
            return False
        try:
            data = read_json_or_none(self._store)
            if not isinstance(data, dict) or data.get("signature") != signature:
                return False
            sent = datetime.fromisoformat(str(data["ts"]))
            return timedelta(0) <= now - sent < URGENT_THROTTLE
        except (ValueError, KeyError, TypeError, OSError):
            return False

    def _record(self, signature: str, now: datetime) -> None:
        if self._store is None:
            return
        try:
            write_json_atomic(
                self._store, {"signature": signature, "ts": now.isoformat()}
            )
        except OSError as exc:
            log.warning("urgent throttle not recorded: %s", type(exc).__name__)


def _signature(summary: Summary) -> str:
    return json.dumps([sorted(summary.anomalies), summary.dead_man])


class DigestChannel:
    """[R57] ``DigestPort``: the weekly digest via the normal notifier.

    ``last_digest_ts`` lives in the state dir; an unreadable file means
    "never sent", so a broken file costs at most one extra digest.
    """

    def __init__(self, notifier: FanOutNotifier, path: Path) -> None:
        self._notifier = notifier
        self._path = path

    def last_sent(self) -> datetime | None:
        try:
            data = read_json_or_none(self._path)
            if not isinstance(data, dict):
                return None
            return datetime.fromisoformat(str(data["last_digest_ts"]))
        except (KeyError, ValueError, TypeError, OSError):
            return None

    def send(self, text: str) -> bool:
        return self._notifier.notify_text(text)

    def record_sent(self, when: datetime) -> None:
        write_json_atomic(self._path, {"last_digest_ts": when.isoformat()})
