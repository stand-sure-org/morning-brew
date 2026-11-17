"""Caveat hashing (pure): an unchanged caveat is not a signal."""

import hashlib


def caveat_hash(text: str | None) -> str | None:
    if text is None:
        return None
    normalized = " ".join(text.split())
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
