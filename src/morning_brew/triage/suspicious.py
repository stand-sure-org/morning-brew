"""Deterministic suspicious-text flag (FR-051).

Runs on the raw caveat before any stripping and before any model call. A
flagged caveat is held; no model ever reads it.
"""

import re

UNTRUSTED_CAP = 2000

# ESC, C0 (except tab and newline), DEL, C1 (incl. 8-bit CSI), bare CR.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]|\r(?!\n)")
# Invisible and direction-changing code points: zero-width, bidi embeddings
# and isolates, BOM, word joiner, and Unicode tag characters (ASCII smuggling).
INVISIBLE_CHARS = re.compile(
    "[\u00ad\u061c\u180e\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\U000e0000-\U000e007f]"
)
# Homoglyph smuggling: full-width forms anywhere, or a word mixing ASCII
# letters with Greek/Cyrillic letters ("Syst\u0435m", "\u043cigrate").
_HOMOGLYPH = re.compile(
    "[\uff00-\uffef]|[A-Za-z][\u0370-\u03ff\u0400-\u052f]|[\u0370-\u03ff\u0400-\u052f][A-Za-z]"
)
# Each alternative is chosen to be absent from ordinary caveat prose: a false
# positive holds a real package until its next version.
_PATTERNS = re.compile(
    r"</?\s*untrusted\b|untrusted-"  # the prompt's delimiter shape
    r"|\b(?:system|assistant|developer)\s*:"  # role-play prefixes
    r"|#{2,}\s*instruction"
    r"|\bignor\w*\s+(?:\w+\s+){0,3}?(?:instruc|anweisung|consign)"  # en/fr/de
    r"|\bdisregard\s+(?:\w+\s+){0,3}?(?:instruc|above|previous|prior)"
    r"|\bverdict\b"
    r"|\b(?:respond|reply|answer)\s+(?:only\s+)?with\s+[\"'`]?upgrade\b"
    r"|\bassistant\b|\bai\s+(?:model|assistant|reviewer)\b"
    r"|\breviewer|\breviewing\s+(?:model|assistant|agent)\b"
    r"|boilerplate_only|mentions_(?:migration|data_format|restart|manual_step)"
    # Keywords spelled out letter by letter ("S Y S T E M", "I-g-n-o-r-e").
    r"|\b(?:[a-z][ -]){5,}[a-z]\b",
    re.IGNORECASE | re.ASCII,
)


def suspicious_text(text: str | None) -> bool:
    if not text:
        return False
    if len(text) > UNTRUSTED_CAP:
        return True
    if _CONTROL.search(text) or INVISIBLE_CHARS.search(text):
        return True
    if _HOMOGLYPH.search(text):
        return True
    return _PATTERNS.search(text) is not None
