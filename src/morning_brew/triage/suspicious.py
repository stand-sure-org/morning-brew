"""Deterministic suspicious-text flag (FR-051).

Runs on the raw caveat before any stripping and before any model call. A
flagged caveat is held; no model ever reads it.
"""

import re
import unicodedata

UNTRUSTED_CAP = 2000

# ESC, C0 (except tab and newline), DEL, C1 (incl. 8-bit CSI), bare CR.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]|\r(?!\n)")
# Invisible code points, by Unicode category rather than a hand list: every
# format char (Cf: zero-width, bidi, BOM, tag chars, interlinear annotation),
# every non-spacing or enclosing mark (Mn/Me: variation selectors, CGJ,
# Mongolian FVS, Khmer inherent vowels, combining marks), plus the line and
# paragraph separators, the braille blank and the Hangul fillers, which
# render as nothing. Unassigned code points (Cn) count too: several ranges
# are reserved as default-ignorable and render as nothing when assigned.
# Cn also covers code points assigned after this Python's unicodedata
# version: they look unassigned here, so they are flagged and fail safe (hold).
_INVISIBLE_CATEGORIES = frozenset({"Cf", "Mn", "Me", "Cn"})
_INVISIBLE_EXTRA = frozenset(
    [0x2028, 0x2029, 0x2800, 0x115F, 0x1160, 0x3164, 0xFFA0, 0x180E, 0x034F]
    + list(range(0xFE00, 0xFE10))
    + list(range(0xE0100, 0xE01F0))
    + list(range(0x180B, 0x1810))
    + [0x17B4, 0x17B5, 0xFFF9, 0xFFFA, 0xFFFB]
    + list(range(0xE0000, 0xE0080))
)


def is_invisible(ch: str) -> bool:
    return (
        ord(ch) in _INVISIBLE_EXTRA or unicodedata.category(ch) in _INVISIBLE_CATEGORIES
    )


def strip_invisible(text: str) -> str:
    return "".join(ch for ch in text if not is_invisible(ch))


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
    r"|##\s*instruction"
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
    if _CONTROL.search(text) or any(is_invisible(ch) for ch in text):
        return True
    if _HOMOGLYPH.search(text):
        return True
    return _PATTERNS.search(text) is not None
