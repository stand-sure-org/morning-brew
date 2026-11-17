"""Loopback-only, digest-pinned Ollama client (FR-051 transport, FR-055).

The local model is only a recorded second opinion, but its client is still
defensive: it talks to loopback only, ignores proxy environment variables,
refuses to run when the model tag no longer points at the pinned digest (a
LAN peer could replace it through ``/api/create``), and maps every failure to
``ModelUnavailable`` or ``ModelInvalidOutput``. Prompt text and response
bodies are untrusted and are never logged; only statuses and exception type
names are.
"""

import http.client
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from morning_brew.domain import (
    CommandResult,
    CommandRunner,
    ModelInvalidOutput,
    ModelUnavailable,
)
from morning_brew.triage.prompt import Prompt
from morning_brew.triage.schemas import RiskAssessment

log = logging.getLogger(__name__)

PORT = 11434
CALL_TIMEOUT = 120
MAX_BODY = 1_048_576
LSOF_TIMEOUT = 10
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_LISTENER_ADDR = re.compile(r"(\S+):%d\b" % PORT)
_LOOPBACK_ADDRS = frozenset({"127.0.0.1", "[::1]", "localhost"})
_LSOF_ARGV = ("lsof", "-nP", f"-iTCP:{PORT}", "-sTCP:LISTEN")


@dataclass(frozen=True)
class OllamaStatus:
    up: bool
    digest_ok: bool
    off_loopback: bool


class _Rejected(ModelInvalidOutput):
    """HTTP 4xx: the request itself was refused, so retrying cannot help."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def _default_opener() -> Callable[..., Any]:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect()
    ).open


def _validate_base_url(base_url: str) -> str:
    try:
        parts = urllib.parse.urlsplit(base_url)
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError on a malformed port
    except ValueError as exc:
        raise ValueError("invalid Ollama base_url") from exc
    if parts.scheme != "http" or host not in _LOOPBACK_HOSTS:
        raise ValueError(
            "Ollama base_url must be http on 127.0.0.1, localhost or [::1]"
        )
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise ValueError("Ollama base_url must not carry userinfo")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("Ollama base_url must be a bare origin")
    return base_url.rstrip("/")


def _norm_digest(value: object) -> str:
    text = value if isinstance(value, str) else ""
    return text.removeprefix("sha256:").strip().lower()


class OllamaClient:
    def __init__(
        self,
        model: str,
        expected_digest: str | None,
        *,
        base_url: str = "http://127.0.0.1:11434",
        opener: Callable[..., Any] | None = None,
        runner: CommandRunner | None = None,
    ):
        self._base = _validate_base_url(base_url)
        self._model = model
        self._expected = _norm_digest(expected_digest) or None
        self._opener = opener if opener is not None else _default_opener()
        self._runner = runner
        self._digest_result: bool | None = None

    # transport -----------------------------------------------------------

    def _call(self, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        """One HTTP call returning the decoded JSON object.

        Raises ModelUnavailable for transport/5xx, ModelInvalidOutput for 4xx
        and for a body that is not a JSON object.
        """
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            self._base + path,
            data=data,
            method="POST" if payload is not None else "GET",
            headers={"Content-Type": "application/json"},
        )
        try:
            with self._opener(request, timeout=CALL_TIMEOUT) as resp:
                raw = resp.read(MAX_BODY + 1)
        except urllib.error.HTTPError as exc:
            log.warning("ollama %s: HTTP %s", path, exc.code)
            if 400 <= exc.code < 500:
                raise _Rejected(f"ollama rejected request: HTTP {exc.code}") from exc
            raise ModelUnavailable(f"ollama HTTP {exc.code}") from exc
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            OSError,
            http.client.HTTPException,
        ) as exc:
            log.warning("ollama %s: %s", path, type(exc).__name__)
            raise ModelUnavailable(f"ollama unreachable: {type(exc).__name__}") from exc
        if len(raw) > MAX_BODY:
            raise ModelInvalidOutput("ollama response too large")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            raise ModelInvalidOutput("ollama response is not JSON") from exc
        if not isinstance(body, dict):
            raise ModelInvalidOutput("ollama response is not an object")
        return body

    # digest pin ----------------------------------------------------------

    def _digest_matches(self) -> bool:
        """True when the model tag resolves to the pinned digest (cached)."""
        if self._digest_result is not None:
            return self._digest_result
        body = self._call("/api/tags", None)
        models = body.get("models")
        found: str | None = None
        if isinstance(models, list):
            for entry in models:
                if isinstance(entry, dict) and entry.get("name") == self._model:
                    found = _norm_digest(entry.get("digest"))
                    break
        ok = bool(found) and found == self._expected
        if self._expected is not None and not ok:
            log.warning("ollama digest pin mismatch or model missing (%s)", self._model)
        self._digest_result = ok
        return ok

    # public --------------------------------------------------------------

    def check_available(self) -> OllamaStatus:
        off = self._off_loopback()
        try:
            ok = self._digest_matches()
        except (ModelUnavailable, ModelInvalidOutput):
            return OllamaStatus(up=False, digest_ok=False, off_loopback=off)
        return OllamaStatus(up=True, digest_ok=ok, off_loopback=off)

    def assess(self, prompt: Prompt) -> RiskAssessment:
        if self._expected is None:
            raise ModelUnavailable("digest not pinned")
        if not self._digest_matches():
            raise ModelUnavailable("digest mismatch")
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
            "stream": False,
            "think": False,
            "format": RiskAssessment.model_json_schema(),
            "options": {"temperature": 0, "num_predict": 400},
            "keep_alive": "5m",
        }
        for attempt in (1, 2):
            try:
                return self._parse(self._call("/api/chat", payload))
            except ModelInvalidOutput as exc:
                if attempt == 2 or isinstance(exc, _Rejected):
                    raise
                log.warning("ollama output invalid; retrying once")
        raise ModelInvalidOutput("unreachable")  # pragma: no cover

    @staticmethod
    def _parse(body: dict[str, Any]) -> RiskAssessment:
        if body.get("done_reason") == "length":
            raise ModelInvalidOutput("ollama output truncated")
        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ModelInvalidOutput("ollama response has no message content")
        try:
            return RiskAssessment.model_validate_json(content, strict=True)
        except ValidationError as exc:
            raise ModelInvalidOutput("ollama output failed schema validation") from exc

    # listener audit ------------------------------------------------------

    def _off_loopback(self) -> bool:
        if self._runner is None:
            return False
        try:
            result: CommandResult = self._runner(_LSOF_ARGV, LSOF_TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - audit must never break triage
            log.warning("listener audit failed: %s", type(exc).__name__)
            return False
        for line in result.stdout.splitlines():
            for match in _LISTENER_ADDR.finditer(line):
                if match.group(1) not in _LOOPBACK_ADDRS:
                    return True
        return False
