"""Version classifier: (installed, current) strings to a Bump class. Pure, never raises."""

import re
from collections.abc import Sequence

from morning_brew.domain import Bump

_VERSION = re.compile(r"^(\d+(?:\.\d+)*)(?:[_-](\d+))?$")


def _parse(version: str) -> tuple[tuple[int, ...], int | None] | None:
    match = _VERSION.match(version.split(",", 1)[0])
    if match is None:
        return None
    numbers = tuple(int(part) for part in match.group(1).split("."))
    suffix = int(match.group(2)) if match.group(2) is not None else None
    return numbers, suffix


def classify_bump(installed: str, current: str) -> Bump:
    old = _parse(installed)
    new = _parse(current)
    if old is None or new is None:
        return Bump.UNPARSEABLE
    width = max(len(old[0]), len(new[0]))
    a = old[0] + (0,) * (width - len(old[0]))
    b = new[0] + (0,) * (width - len(new[0]))
    for index, (x, y) in enumerate(zip(a, b, strict=True)):
        if x == y:
            continue
        if index == 0:
            return Bump.MAJOR
        if index == 1:
            return Bump.ZERO_MAJOR_MINOR if a[0] == 0 else Bump.MINOR
        return Bump.PATCH
    return Bump.REVISION


def _sort_key(version: str) -> tuple[tuple[int, ...], int] | None:
    parsed = _parse(version)
    if parsed is None:
        return None
    numbers = list(parsed[0])
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers), parsed[1] or 0


def newest_installed(installed_versions: Sequence[str]) -> str | None:
    if not installed_versions:
        return None
    keyed = [(key, v) for v in installed_versions if (key := _sort_key(v)) is not None]
    if not keyed:
        return installed_versions[-1]
    return max(keyed, key=lambda pair: pair[0])[1]
