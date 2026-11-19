"""Reconcile the hold ledger with the owner's pins (SPEC FR-062).

The owner's pin wins: a package the owner pinned since the last run loses its
hold row so the ledger never claims ownership of it (the rules then report it
as SKIP). This never calls brew and never pins or unpins anything.
"""

from morning_brew.domain import Snapshot
from morning_brew.ports import HoldStorePort


def reconcile(snapshot: Snapshot, holds: HoldStorePort) -> list[str]:
    released: list[str] = []
    for candidate in snapshot.candidates:
        if not candidate.pinned:
            continue
        if holds.get(candidate.full_name) is None:
            continue
        holds.release(candidate.full_name)
        released.append(candidate.full_name)
    return released
