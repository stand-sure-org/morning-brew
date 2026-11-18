"""Per-package hold ledger: one JSON file per package under ``root/holds/``."""

import contextlib
import logging
import os
import tempfile
import urllib.parse
from datetime import datetime
from pathlib import Path

from morning_brew.domain import Hold, HoldKind, StateUnwritable
from morning_brew.sanitize import redact
from morning_brew.store.atomic import read_json_or_none, write_json_atomic

log = logging.getLogger(__name__)


def needs_retriage(hold: Hold, current_version: str) -> bool:
    return hold.held_at_version != current_version


def is_suppressed(hold: Hold, current_version: str) -> bool:
    return hold.skipped_for_version == current_version


def _to_json(hold: Hold) -> dict:
    return {
        "name": hold.name,
        "kind": hold.kind.value,
        "held_at_version": hold.held_at_version,
        "reason": hold.reason,
        "first_held": hold.first_held.isoformat(),
        "blocked_by": hold.blocked_by,
        # [M1] derived from untrusted text; never stored with a secret in it
        "model_reasoning": (
            redact(hold.model_reasoning) if hold.model_reasoning is not None else None
        ),
        "skipped_for_version": hold.skipped_for_version,
    }


def _opt_str(raw: dict, key: str) -> str | None:
    value = raw.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(key)
    return value


def _from_json(raw: object) -> Hold | None:
    if not isinstance(raw, dict):
        return None
    try:
        name = raw["name"]
        held_at = raw["held_at_version"]
        reason = raw["reason"]
        if not all(isinstance(v, str) for v in (name, held_at, reason)):
            return None
        return Hold(
            name=name,
            kind=HoldKind(raw["kind"]),
            held_at_version=held_at,
            reason=reason,
            first_held=datetime.fromisoformat(raw["first_held"]),
            blocked_by=_opt_str(raw, "blocked_by"),
            model_reasoning=_opt_str(raw, "model_reasoning"),
            skipped_for_version=_opt_str(raw, "skipped_for_version"),
        )
    except (KeyError, ValueError, TypeError):
        return None


class FileHoldStore:
    def __init__(self, root: Path) -> None:
        self._dir = root / "holds"

    def _ensure_dir(self) -> None:
        self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._dir, 0o700)

    def _path(self, name: str) -> Path:
        return self._dir / (urllib.parse.quote(name, safe="") + ".json")

    def _load(self, path: Path) -> Hold | None:
        if path.is_symlink():
            log.warning("skipping symlinked hold file %s", path)
            return None
        hold = _from_json(read_json_or_none(path))
        if hold is None and path.exists():
            log.warning("ignoring malformed hold file %s", path)
        return hold

    def ensure_writable(self) -> None:
        try:
            self._ensure_dir()
            fd, tmp = tempfile.mkstemp(dir=self._dir, prefix=".probe.", suffix=".tmp")
            os.close(fd)
            os.unlink(tmp)
        except OSError as exc:
            raise StateUnwritable(
                f"cannot write hold store {self._dir}: {exc}"
            ) from exc

    def get(self, name: str) -> Hold | None:
        return self._load(self._path(name))

    def put(self, hold: Hold) -> None:
        self._ensure_dir()
        write_json_atomic(self._path(hold.name), _to_json(hold))

    def release(self, name: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            self._path(name).unlink()

    def list_all(self) -> list[Hold]:
        if not self._dir.is_dir():
            return []
        holds = []
        for path in sorted(self._dir.glob("*.json")):
            hold = self._load(path)
            if hold is not None:
                holds.append(hold)
        return sorted(holds, key=lambda h: h.name)

    def mark_skipped(self, name: str, version: str) -> None:
        hold = self.get(name)
        if hold is None:
            log.info("mark_skipped: no hold for %s; nothing to do", name)
            return
        self.put(
            Hold(
                name=hold.name,
                kind=hold.kind,
                held_at_version=hold.held_at_version,
                reason=hold.reason,
                first_held=hold.first_held,
                blocked_by=hold.blocked_by,
                model_reasoning=hold.model_reasoning,
                skipped_for_version=version,
            )
        )
