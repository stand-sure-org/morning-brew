"""Names brew listed outdated without info on the last run (targeted fix).

An unjoinable formula degrades only the first run it is seen, so a name that
stays unjoinable cannot hold the run degraded (and retrying hourly) forever.
An unreadable file means "none seen": at worst one extra degraded run.
"""

import logging
from pathlib import Path

from morning_brew.store.atomic import read_json_or_none, write_json_atomic

log = logging.getLogger(__name__)


class FileUnjoinableStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def read(self) -> frozenset[str]:
        try:
            data = read_json_or_none(self._path)
        except (OSError, ValueError):
            return frozenset()
        if not isinstance(data, list):
            return frozenset()
        return frozenset(n for n in data if isinstance(n, str))

    def write(self, names: frozenset[str]) -> None:
        write_json_atomic(self._path, sorted(names))
