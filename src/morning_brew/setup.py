"""One-time owner-confirmed seeding of trusted taps and caveat hashes."""

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from morning_brew.domain import Kind, Snapshot
from morning_brew.ports import SeenStorePort
from morning_brew.store.atomic import write_json_atomic
from morning_brew.store.seen import seed_caveat_hashes

log = logging.getLogger(__name__)

TRUSTED_TAPS_FILE = "trusted_taps.json"
_CORE = "homebrew/core"


@dataclass(frozen=True)
class SetupResult:
    trusted_taps: frozenset[str]
    seeded: int


def read_trusted_taps(state_dir: Path) -> frozenset[str]:
    try:
        raw = json.loads((state_dir / TRUSTED_TAPS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(t.lower() for t in raw if isinstance(t, str))


def proposed_taps(snapshot: Snapshot) -> frozenset[str]:
    return frozenset(
        c.tap.lower()
        for c in snapshot.candidates
        if c.kind is Kind.FORMULA
        and c.installed is not None
        and c.tap
        and c.tap.lower() != _CORE
    )


def run_setup(
    snapshot: Snapshot,
    installed_caveats: Mapping[str, str | None],
    seen: SeenStorePort,
    state_dir: Path,
    confirm: Callable[[str], bool],
    digest_hint: str | None = None,
) -> SetupResult:
    taps = proposed_taps(snapshot)
    with_caveats = sum(1 for t in installed_caveats.values() if t and t.strip())
    lines = ["morning-brew setup will:"]
    if taps:
        lines.append("  trust these taps: " + ", ".join(sorted(taps)))
    else:
        lines.append("  trust no extra taps (only homebrew/core)")
    lines.append(
        f"  record caveat hashes for {with_caveats} installed formulae "
        "(existing hashes are never overwritten)"
    )
    if digest_hint:
        lines.append(digest_hint)
    if not confirm("\n".join(lines)):
        return SetupResult(frozenset(), 0)
    write_json_atomic(state_dir / TRUSTED_TAPS_FILE, sorted(taps))
    seeded = seed_caveat_hashes(seen, installed_caveats)
    return SetupResult(taps, seeded)
