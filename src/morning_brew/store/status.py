"""Last run summary persisted as JSON (file 0600) for ``morning-brew status``."""

import dataclasses
import logging
from pathlib import Path

from morning_brew.domain import Summary
from morning_brew.store.atomic import read_json_or_none, write_json_atomic

log = logging.getLogger(__name__)

_FIELDS = {f.name: f for f in dataclasses.fields(Summary)}
_TUPLES = frozenset(
    {
        "upgraded",
        "failed",
        "self_dep_commands",
        "disagreements",
        "live_old_binary",
        "anomalies",
        "degraded_reasons",
    }
)
_INTS = frozenset({"decided_holds", "blocked_by_dep", "transient"})


def _from_json(raw: object) -> Summary | None:
    if not isinstance(raw, dict) or set(raw) != set(_FIELDS):
        return None
    values = dict(raw)
    for key in _TUPLES:
        items = values[key]
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            return None
        values[key] = tuple(items)
    for key in _INTS:
        if not isinstance(values[key], int) or isinstance(values[key], bool):
            return None
    oldest = values["oldest_decided_hold_days"]
    if oldest is not None and (not isinstance(oldest, int) or isinstance(oldest, bool)):
        return None
    if not isinstance(values["run_id"], str):
        return None
    if values["quality"] not in ("full", "degraded"):
        return None
    if not isinstance(values["dead_man"], bool):
        return None
    return Summary(**values)


class FileStatusStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def write(self, summary: Summary) -> None:
        write_json_atomic(self._path, dataclasses.asdict(summary))

    def read(self) -> Summary | None:
        raw = read_json_or_none(self._path)
        if raw is None:
            return None
        summary = _from_json(raw)
        if summary is None:
            log.warning("ignoring malformed status file %s", self._path)
        return summary
