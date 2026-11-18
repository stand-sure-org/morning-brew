"""Secret redaction for audit text.

Vendored (copied, not imported) from /Volumes/Repos/phlegyas/phlegyas/sanitize.py
at phlegyas commit ba08532. The marker and pattern set follow that file;
`api key` additionally matches with a space separator. Local changes [M1]:
quoted values are redacted up to their closing quote, bare values up to the
next whitespace (not stopping at commas), and `Authorization: Basic` is added.
"""

import re

REDACTED = "***REDACTED***"

# [M1] A value is either quoted (redacted up to its closing quote on the same
# line, quotes kept so JSON stays valid) or bare (redacted up to the next
# whitespace, commas included). A leading auth scheme word (Bearer/Basic) of a
# bare value is kept and the credential after it is the value. Every
# alternative is bounded by a quote, whitespace or the line, so this is linear,
# and the marker re-redacts to itself, so ``redact`` is idempotent.
_VALUE = (
    r"(?:\"(?P<dq>[^\"\n]*)\""
    r"|'(?P<sq>[^'\n]*)'"
    r"|(?P<q>[\"']?)(?P<scheme>(?:Bearer|Basic)[ \t]+)?(?P<bare>[^\s\"']\S*))"
)

_VALUE_PATTERNS = [
    # keyword secrets: optional quotes, REQUIRED [:=] separator (prose is
    # untouched).
    re.compile(
        r"(?P<pre>[\"']?(?:password|secret|token|api[_\s-]?key)[\"']?\s*[:=]\s*)"
        + _VALUE,
        re.IGNORECASE,
    ),
    # env-var secrets: name chars only, an optional closing quote (JSON or
    # dict key), REQUIRED [:=] separator, horizontal whitespace only.
    re.compile(
        r"(?P<pre>AWS_SECRET[A-Z0-9_]*[\"']?[ \t]*[:=][ \t]*)" + _VALUE,
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<pre>AWS_SESSION_TOKEN[A-Z0-9_]*[\"']?[ \t]*[:=][ \t]*)" + _VALUE,
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<pre>ANTHROPIC_API_KEY[A-Z0-9_]*[\"']?[ \t]*[:=][ \t]*)" + _VALUE,
        re.IGNORECASE,
    ),
]

_SENSITIVE_PATTERNS = [
    # HTTP basic auth header; "Basic" alone (prose) is never a trigger.
    re.compile(
        r"(Authorization[\"']?\s*[:=]\s*[\"']?Basic[ \t]+)([^\s\"']+)", re.IGNORECASE
    ),
    re.compile(r"(Bearer\s+)(\S+)", re.IGNORECASE),
    re.compile(r"(sk-ant-\S{4})\S+", re.IGNORECASE),
    re.compile(r"(xoxb-\S{4})\S+", re.IGNORECASE),
    re.compile(r"(ghp_)\S+", re.IGNORECASE),
    re.compile(r"(github_pat_)\S+", re.IGNORECASE),
    # URL-embedded credentials (user:password@host); the @ is kept via lookahead.
    re.compile(r"(://[^:@]+:)\S+(?=@)", re.IGNORECASE),
]


def _value_sub(m: re.Match[str]) -> str:
    pre = m.group("pre")
    if m.group("dq") is not None:
        return f'{pre}"{REDACTED}"'
    if m.group("sq") is not None:
        return f"{pre}'{REDACTED}'"
    return pre + m.group("q") + (m.group("scheme") or "") + REDACTED


def redact(text: str) -> str:
    """Mask secret-shaped substrings with ``***REDACTED***``.

    Idempotent: ``redact(redact(x)) == redact(x)``.
    """
    out = text
    for pattern in _VALUE_PATTERNS:
        out = pattern.sub(_value_sub, out)
    for pattern in _SENSITIVE_PATTERNS:
        out = pattern.sub(lambda m: m.group(1) + REDACTED, out)
    return out


def tail_redacted(text: str, limit: int = 2000) -> str:
    """Redact first, then keep the last ``limit`` characters."""
    redacted = redact(text)
    return redacted[-limit:] if limit > 0 else ""
