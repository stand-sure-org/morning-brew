"""Parsers for brew's human-readable output."""

import re

_HEADER = "==>"
_SECTION = "Would upgrade"
_ROW = re.compile(r"^(\S+)\s+.+\s->\s+.+$")


def parse_dry_run(stdout: str) -> frozenset[str]:
    """Names listed under every "Would upgrade" section of a dry-run upgrade.

    Real brew 7 headers carry no trailing colon, e.g.
    ``==> Would upgrade 1 requested outdated package`` and
    ``==> Would upgrade 2 dependents``; rows are ``<name> <old> -> <new> (size)``
    with column padding. Any other ``==>`` header ends the section, so names
    from e.g. a "Would install" section never leak into the closure.
    """
    names: set[str] = set()
    in_section = False
    for raw in stdout.splitlines():
        line = raw.strip()
        if line.startswith(_HEADER) or _SECTION in line:
            in_section = _SECTION in line
            continue
        if not in_section:
            continue
        match = _ROW.match(line)
        if match:
            names.add(match.group(1))
    return frozenset(names)
