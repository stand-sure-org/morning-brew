"""Append-only JSONL audit log. Reasoning and action_result are redacted on write."""

import json
import logging
import os
import stat
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from morning_brew.domain import AuditEvent
from morning_brew.sanitize import redact

log = logging.getLogger(__name__)

_FIELDS = (
    "run_id",
    "ts",
    "brew_version",
    "stage",
    "package",
    "rule_id",
    "verdict",
    "action_result",
    "models",
    "booleans",
    "reasoning",
    "component",
    "hold_kind",
)
_REQUIRED = ("run_id", "ts", "brew_version", "stage")


def _to_dict(event: AuditEvent) -> dict[str, Any]:
    return {
        "run_id": event.run_id,
        "ts": event.ts.isoformat(),
        "brew_version": event.brew_version,
        "stage": event.stage,
        "package": event.package,
        "rule_id": event.rule_id,
        "verdict": event.verdict,
        "action_result": event.action_result,
        "models": [list(m) for m in event.models],
        "booleans": None if event.booleans is None else dict(event.booleans),
        "reasoning": event.reasoning,
        "component": event.component,
        "hold_kind": event.hold_kind,
    }


def _from_dict(raw: dict[str, Any]) -> AuditEvent:
    missing = [k for k in _REQUIRED if k not in raw]
    if missing:
        raise KeyError(missing[0])
    booleans = raw.get("booleans")
    return AuditEvent(
        run_id=raw["run_id"],
        ts=datetime.fromisoformat(raw["ts"]),
        brew_version=raw["brew_version"],
        stage=raw["stage"],
        package=raw.get("package"),
        rule_id=raw.get("rule_id"),
        verdict=raw.get("verdict"),
        action_result=raw.get("action_result"),
        models=tuple((str(m[0]), m[1]) for m in raw.get("models") or ()),
        booleans=None if booleans is None else dict(booleans),
        reasoning=raw.get("reasoning"),
        component=raw.get("component"),
        hold_kind=raw.get("hold_kind"),
    )


class JsonlAudit:
    def __init__(self, path: Path) -> None:
        self._path = path

    def record(self, event: AuditEvent) -> None:
        safe = replace(
            event,
            reasoning=None if event.reasoning is None else redact(event.reasoning),
            action_result=None
            if event.action_result is None
            else redact(event.action_result),
        )
        line = json.dumps(_to_dict(safe), sort_keys=False, ensure_ascii=False) + "\n"
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "ab") as fh:
            if stat.S_IMODE(os.fstat(fd).st_mode) & 0o077:
                os.fchmod(fd, 0o600)
            if not self._ends_with_newline():
                fh.write(b"\n")  # isolate a partial line left by an interrupted append
            fh.write(line.encode("utf-8"))
            fh.flush()

    def _ends_with_newline(self) -> bool:
        try:
            with open(self._path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                if fh.tell() == 0:
                    return True
                fh.seek(-1, os.SEEK_END)
                return fh.read(1) == b"\n"
        except FileNotFoundError:
            return True

    def events_for(self, package: str) -> list[AuditEvent]:
        """Return events for ``package`` in append (file) order."""
        try:
            data = self._path.read_bytes()
        except FileNotFoundError:
            return []
        out: list[AuditEvent] = []
        for lineno, chunk in enumerate(data.split(b"\n"), start=1):
            if not chunk.strip():
                continue
            try:
                raw = json.loads(chunk.decode("utf-8"))
                if not isinstance(raw, dict):
                    raise TypeError("not an object")
                if raw.get("package") != package:
                    continue
                out.append(_from_dict(raw))
            except (ValueError, KeyError, TypeError, IndexError):
                log.warning("skipping corrupt audit line %d in %s", lineno, self._path)
        return out
