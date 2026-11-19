"""Non-blocking run lock via fcntl.flock."""

import fcntl
import os
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path

from morning_brew.domain import LockHeld


class FileLock:
    def __init__(self, path: Path) -> None:
        self._path = path

    def acquire(self) -> AbstractContextManager[None]:
        return self._held()

    @contextmanager
    def _held(self) -> Iterator[None]:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LockHeld(str(self._path)) from exc
            yield
        finally:
            os.close(fd)  # closing releases the flock
