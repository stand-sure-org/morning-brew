"""One-time owner-confirmed seeding of trusted taps and caveat hashes."""

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from morning_brew.domain import (
    EXIT_OK,
    Kind,
    ModelInvalidOutput,
    ModelUnavailable,
    Snapshot,
)
from morning_brew.ports import SeenStorePort
from morning_brew.store.atomic import write_json_atomic
from morning_brew.store.seen import seed_caveat_hashes
from morning_brew.textsafe import strip_control

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


def setup_command(
    *,
    snapshot: Callable[[], Snapshot],
    installed_caveats: Callable[[], Mapping[str, str | None]],
    seen: SeenStorePort,
    state_dir: Path,
    ask: Callable[[str], str],
    say: Callable[[str], None],
    ollama_digest: Callable[[], str | None] | None,
    model: str,
) -> int:
    """[I4] ``morning-brew setup``: a fresh snapshot, then the owner's y/N."""
    snap = snapshot()
    caveats = installed_caveats()
    hint = None
    if ollama_digest is not None:
        try:
            digest = ollama_digest()
        except (ModelUnavailable, ModelInvalidOutput):
            digest = None  # Ollama down: no hint, setup goes on
        if digest:
            hint = (
                f"  Ollama is up and {strip_control(model)} has digest {digest};\n"
                f'  to pin it, set local_digest = "sha256:{digest}" in config.toml'
            )
    confirmed = False

    def confirm(text: str) -> bool:
        nonlocal confirmed
        say(text)
        try:
            answer = ask("Proceed? [y/N] ")
        except EOFError:
            answer = ""
        confirmed = answer.strip().lower() == "y"
        return confirmed

    result = run_setup(snap, caveats, seen, state_dir, confirm, digest_hint=hint)
    if not confirmed:
        say("Setup cancelled; nothing written.")
        return EXIT_OK
    taps = ", ".join(sorted(result.trusted_taps)) or "none"
    say(f"Trusted taps: {taps}. Caveat hashes recorded: {result.seeded}.")
    return EXIT_OK
