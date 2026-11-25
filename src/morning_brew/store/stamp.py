"""Success stamp persisted as JSON (file 0600)."""

import logging
from datetime import datetime
from pathlib import Path

from morning_brew.domain import Stamp
from morning_brew.store.atomic import read_json_or_none, write_json_atomic

log = logging.getLogger(__name__)


def _dt(value: object) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


class FileStampStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def read(self) -> Stamp | None:
        raw = read_json_or_none(self._path)
        if not isinstance(raw, dict):
            return None
        try:
            quality = raw["quality"]
            full_runs = raw["full_runs"]
            if quality not in ("full", "degraded") or not isinstance(full_runs, int):
                raise ValueError("bad stamp fields")
            return Stamp(
                last_full_success=_dt(raw.get("last_full_success")),
                last_attempt=_dt(raw.get("last_attempt")),
                quality=quality,
                full_runs=full_runs,
                first_attempt=_dt(raw.get("first_attempt")),
            )
        except (KeyError, ValueError):
            log.warning("ignoring malformed stamp %s", self._path)
            return None

    def write(self, stamp: Stamp) -> None:
        write_json_atomic(
            self._path,
            {
                "last_full_success": _iso(stamp.last_full_success),
                "last_attempt": _iso(stamp.last_attempt),
                "quality": stamp.quality,
                "full_runs": stamp.full_runs,
                "first_attempt": _iso(stamp.first_attempt),
            },
        )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
