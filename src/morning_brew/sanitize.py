"""Secret redaction for audit text.

Vendored (copied, not imported) from /Volumes/Repos/phlegyas/phlegyas/sanitize.py
at phlegyas commit ba08532. The marker and pattern set follow that file;
`api key` additionally matches with a space separator.
"""

import re

REDACTED = "***REDACTED***"

_SENSITIVE_PATTERNS = [
    # keyword secrets: optional quotes, REQUIRED [:=] separator (prose is
    # untouched). A leading auth scheme word (Bearer/Basic) is kept and the
    # credential after it is the value, so the scheme never stands in for it.
    re.compile(
        r"([\"']?(?:password|secret|token|api[_\s-]?key)[\"']?\s*[:=]\s*[\"']?"
        r"(?:(?:Bearer|Basic)[ \t]+)?)([^\s\"',]+)",
        re.IGNORECASE,
    ),
    # env-var secrets: name chars only, an optional closing quote (JSON or
    # dict key), REQUIRED [:=] separator, horizontal whitespace only.
    # Linear, and idempotent (the marker re-redacts to itself).
    re.compile(
        r"(AWS_SECRET[A-Z0-9_]*[\"']?[ \t]*[:=][ \t]*[\"']?)([^\s\"',]+)", re.IGNORECASE
    ),
    re.compile(
        r"(AWS_SESSION_TOKEN[A-Z0-9_]*[\"']?[ \t]*[:=][ \t]*[\"']?)([^\s\"',]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(ANTHROPIC_API_KEY[A-Z0-9_]*[\"']?[ \t]*[:=][ \t]*[\"']?)([^\s\"',]+)",
        re.IGNORECASE,
    ),
    re.compile(r"(Bearer\s+)(\S+)", re.IGNORECASE),
    re.compile(r"(sk-ant-\S{4})\S+", re.IGNORECASE),
    re.compile(r"(xoxb-\S{4})\S+", re.IGNORECASE),
    re.compile(r"(ghp_)\S+", re.IGNORECASE),
    re.compile(r"(github_pat_)\S+", re.IGNORECASE),
    # URL-embedded credentials (user:password@host); the @ is kept via lookahead.
    re.compile(r"(://[^:@]+:)\S+(?=@)", re.IGNORECASE),
]


def redact(text: str) -> str:
    """Mask secret-shaped substrings with ``***REDACTED***``.

    Idempotent: ``redact(redact(x)) == redact(x)``.
    """
    out = text
    for pattern in _SENSITIVE_PATTERNS:
        out = pattern.sub(lambda m: m.group(1) + REDACTED, out)
    return out


def tail_redacted(text: str, limit: int = 2000) -> str:
    """Redact first, then keep the last ``limit`` characters."""
    redacted = redact(text)
    return redacted[-limit:] if limit > 0 else ""
