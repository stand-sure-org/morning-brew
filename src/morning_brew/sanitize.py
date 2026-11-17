"""Secret redaction for audit text.

Vendored (copied, not imported) from /Volumes/Repos/phlegyas/phlegyas/sanitize.py
at phlegyas commit ba08532. The marker and pattern set follow that file;
`api key` additionally matches with a space separator.
"""

import re

REDACTED = "***REDACTED***"

_SENSITIVE_PATTERNS = [
    # keyword secrets: optional quotes, REQUIRED [:=] separator (prose is untouched)
    re.compile(
        r"([\"']?(?:password|secret|token|api[_\s-]?key)[\"']?\s*[:=]\s*[\"']?)([^\s\"',]+)",
        re.IGNORECASE,
    ),
    re.compile(r"(AWS_SECRET\S*\s*=?\s*)(\S+)", re.IGNORECASE),
    re.compile(r"(AWS_SESSION_TOKEN\s*=?\s*)(\S+)", re.IGNORECASE),
    re.compile(r"(ANTHROPIC_API_KEY\s*=?\s*)(\S+)", re.IGNORECASE),
    re.compile(r"(Bearer\s+)(\S+)", re.IGNORECASE),
    re.compile(r"(sk-ant-\S{4})\S+", re.IGNORECASE),
    re.compile(r"(xoxb-\S{4})\S+", re.IGNORECASE),
    re.compile(r"(ghp_)\S+", re.IGNORECASE),
    re.compile(r"(github_pat_)\S+", re.IGNORECASE),
    # URL-embedded credentials (user:password@host); the @ is kept via lookahead.
    re.compile(r"(://[^:@]+:)\S+(?=@)", re.IGNORECASE),
]


def redact(text: str) -> str:
    """Mask secret-shaped substrings with ``***REDACTED***``."""
    out = text
    for pattern in _SENSITIVE_PATTERNS:
        out = pattern.sub(lambda m: m.group(1) + REDACTED, out)
    return out


def tail_redacted(text: str, limit: int = 2000) -> str:
    """Redact first, then keep the last ``limit`` characters."""
    redacted = redact(text)
    return redacted[-limit:] if limit > 0 else ""
