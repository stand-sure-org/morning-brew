"""First-seen, caveat hashes and upgrade history in one JSON file (0600)."""

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from morning_brew.caveats import caveat_hash
from morning_brew.ports import SeenStorePort
from morning_brew.store.atomic import read_json_or_none, write_json_atomic

log = logging.getLogger(__name__)


def _empty() -> dict:
    return {"first_seen": {}, "caveat_hash": {}, "upgrades": [], "cleaned": []}


class FileSeenStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def _load(self) -> dict:
        raw = read_json_or_none(self._path)
        if raw is None:
            return _empty()
        if not isinstance(raw, dict):
            log.warning("ignoring malformed seen store %s", self._path)
            return _empty()
        data = _empty()
        first_seen = raw.get("first_seen")
        if isinstance(first_seen, dict):
            data["first_seen"] = {
                n: v
                for n, v in first_seen.items()
                if isinstance(v, dict) and all(isinstance(x, str) for x in v.values())
            }
        hashes = raw.get("caveat_hash")
        if isinstance(hashes, dict):
            data["caveat_hash"] = {
                n: h for n, h in hashes.items() if isinstance(h, str)
            }
        upgrades = raw.get("upgrades")
        if isinstance(upgrades, list):
            data["upgrades"] = [
                u
                for u in upgrades
                if isinstance(u, dict)
                and isinstance(u.get("seq"), int)
                and isinstance(u.get("names"), list)
                and all(isinstance(n, str) for n in u["names"])
            ]
        cleaned = raw.get("cleaned")
        if isinstance(cleaned, list):
            data["cleaned"] = [n for n in cleaned if isinstance(n, str)]
        return data

    def _save(self, data: dict) -> None:
        write_json_atomic(self._path, data)

    def record_first_seen(self, name: str, version: str, now: datetime) -> None:
        data = self._load()
        versions = data["first_seen"].setdefault(name, {})
        if version in versions:
            return
        versions[version] = now.isoformat()
        self._save(data)

    def first_seen(self, name: str, version: str) -> datetime | None:
        value = self._load()["first_seen"].get(name, {}).get(version)
        if value is None:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            log.warning("ignoring bad first_seen timestamp for %s %s", name, version)
            return None

    def caveat_hash(self, name: str) -> str | None:
        return self._load()["caveat_hash"].get(name)

    def record_caveat_hash(self, name: str, h: str) -> None:
        data = self._load()
        data["caveat_hash"][name] = h
        self._save(data)

    def record_upgrade(self, run_seq: int, names: Sequence[str]) -> None:
        data = self._load()
        data["upgrades"].append({"seq": run_seq, "names": list(names)})
        # A fresh upgrade re-arms cleanup for that package.
        data["cleaned"] = [n for n in data["cleaned"] if n not in set(names)]
        self._save(data)

    def due_for_cleanup(self, current_seq: int, runs: int) -> list[str]:
        data = self._load()
        cleaned = set(data["cleaned"])
        threshold = current_seq - runs
        # Latest upgrade seq per name decides eligibility.
        latest: dict[str, int] = {}
        for entry in data["upgrades"]:
            for n in entry["names"]:
                latest[n] = max(latest.get(n, entry["seq"]), entry["seq"])
        return sorted(
            n for n, seq in latest.items() if seq <= threshold and n not in cleaned
        )

    def mark_cleaned(self, names: Sequence[str]) -> None:
        data = self._load()
        data["cleaned"] = sorted(set(data["cleaned"]) | set(names))
        self._save(data)


def seed_caveat_hashes(
    store: SeenStorePort, installed_caveats: Mapping[str, str | None]
) -> int:
    seeded = 0
    for name, text in installed_caveats.items():
        h = caveat_hash(text)
        if h is None:
            continue
        store.record_caveat_hash(name, h)
        seeded += 1
    return seeded
