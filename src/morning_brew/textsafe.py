"""Terminal-safe text: strip escape sequences and control characters."""

import re

# CSI (ESC [ ... final), OSC (ESC ] ... BEL|ST), other two-byte ESC sequences,
# and the 8-bit CSI introducer (U+009B) with its parameters.
_ANSI = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?"
    r"|\x1b[@-Z\\-_]"
    r"|\x9b[0-?]*[ -/]*[@-~]"
)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def strip_control(text: str) -> str:
    """Remove ANSI escapes and C0/C1 control characters except newline and tab.

    A carriage return is dropped, so CRLF collapses to LF.
    """
    return _CONTROL.sub("", _ANSI.sub("", text))
