"""T19: loopback-only, digest-pinned Ollama client (FR-051, FR-055)."""

import io
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

import pytest

from morning_brew.domain import (
    CommandResult,
    ModelInvalidOutput,
    ModelUnavailable,
)
from morning_brew.triage.ollama import OllamaClient, OllamaStatus
from morning_brew.triage.prompt import Prompt
from morning_brew.triage.schemas import RiskAssessment

DIGEST = "a" * 64
GOOD = {
    "mentions_migration": False,
    "mentions_data_format": False,
    "mentions_restart": False,
    "mentions_manual_step": False,
    "boilerplate_only": True,
    "verdict": "upgrade",
    "rationale": "boilerplate",
}
PROMPT = Prompt(system="SYSTEM-TEXT", user="USER-TEXT", delimiter="XYZ")


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def chat_body(content: str, done_reason: str = "stop") -> dict[str, Any]:
    return {
        "message": {"role": "assistant", "content": content},
        "done_reason": done_reason,
    }


class FakeOpener:
    def __init__(self, chat: list[Any], tags: Any = None):
        self.chat = list(chat)
        self.tags = (
            tags
            if tags is not None
            else {"models": [{"name": "qwen3:4b", "digest": DIGEST}]}
        )
        self.requests: list[urllib.request.Request] = []
        self.timeouts: list[float] = []
        self.chat_calls = 0
        self.tags_calls = 0

    def __call__(self, request: urllib.request.Request, timeout: float = 0) -> _Resp:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if request.full_url.endswith("/api/tags"):
            self.tags_calls += 1
            item = self.tags
        else:
            self.chat_calls += 1
            item = self.chat.pop(0)
        if isinstance(item, BaseException):
            raise item
        return _Resp(json.dumps(item).encode() if not isinstance(item, bytes) else item)


def client(
    opener: FakeOpener, digest: str | None = DIGEST, runner: Any = None
) -> OllamaClient:
    return OllamaClient("qwen3:4b", digest, opener=opener, runner=runner)


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://127.0.0.1:11434/api/chat", code, "x", None, io.BytesIO(b"BODY")
    )  # type: ignore[arg-type]


def test_request_body_matches_contract():
    opener = FakeOpener([chat_body(json.dumps(GOOD))])
    result = client(opener).assess(PROMPT)
    assert result == RiskAssessment(**GOOD)
    req = opener.requests[-1]
    assert (
        req.full_url == "http://127.0.0.1:11434/api/chat" and req.get_method() == "POST"
    )
    body = json.loads(req.data)
    assert body["stream"] is False and body["think"] is False
    assert body["options"] == {"temperature": 0, "num_predict": 400}
    assert body["format"] == RiskAssessment.model_json_schema()
    assert body["model"] == "qwen3:4b" and body["keep_alive"] == "5m"
    assert body["messages"] == [
        {"role": "system", "content": "SYSTEM-TEXT"},
        {"role": "user", "content": "USER-TEXT"},
    ]
    assert opener.timeouts[-1] == 120


def test_truncated_json_retries_once_then_invalid():
    opener = FakeOpener([chat_body('{"mentions_mig', "length")] * 3)
    with pytest.raises(ModelInvalidOutput):
        client(opener).assess(PROMPT)
    assert opener.chat_calls == 2


def test_done_reason_length_retries_even_if_json_parses():
    opener = FakeOpener(
        [chat_body(json.dumps(GOOD), "length"), chat_body(json.dumps(GOOD))]
    )
    assert client(opener).assess(PROMPT).verdict == "upgrade"
    assert opener.chat_calls == 2


def test_invalid_then_valid_succeeds():
    opener = FakeOpener([chat_body("not json"), chat_body(json.dumps(GOOD))])
    assert client(opener).assess(PROMPT).boilerplate_only is True


def test_strict_schema_rejects_coercion():
    bad = dict(GOOD, mentions_migration="false")
    opener = FakeOpener([chat_body(json.dumps(bad))] * 2)
    with pytest.raises(ModelInvalidOutput):
        client(opener).assess(PROMPT)


def test_missing_message_is_invalid():
    opener = FakeOpener([{"nope": 1}] * 2)
    with pytest.raises(ModelInvalidOutput):
        client(opener).assess(PROMPT)


@pytest.mark.parametrize(
    "exc",
    [
        urllib.error.URLError("refused"),
        TimeoutError(),
        ConnectionResetError(),
        http_error(500),
        http_error(503),
    ],
)
def test_connection_failures_are_unavailable(exc: BaseException):
    opener = FakeOpener([exc])
    with pytest.raises(ModelUnavailable):
        client(opener).assess(PROMPT)


def test_http_4xx_is_invalid_output():
    opener = FakeOpener([http_error(404)])
    with pytest.raises(ModelInvalidOutput):
        client(opener).assess(PROMPT)
    assert opener.chat_calls == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.5:11434",
        "http://0.0.0.0:11434",
        "http://localhost.evil.com:11434",
        "http://x@evil.com:11434",
        "http://127.0.0.1@evil.com:11434",
        "https://127.0.0.1:11434",
        "ftp://127.0.0.1",
        "http://[::2]:11434",
        "127.0.0.1:11434",
    ],
)
def test_non_loopback_base_url_rejected(url: str):
    with pytest.raises(ValueError):
        OllamaClient("m", None, base_url=url)


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:11434", "http://localhost:11434", "http://[::1]:11434"]
)
def test_loopback_base_urls_accepted(url: str):
    OllamaClient("m", None, base_url=url)


def test_digest_mismatch_disables_stage():
    opener = FakeOpener(
        [chat_body(json.dumps(GOOD))],
        tags={"models": [{"name": "qwen3:4b", "digest": "b" * 64}]},
    )
    c = client(opener)
    assert not c.check_available().digest_ok
    with pytest.raises(ModelUnavailable, match="digest mismatch"):
        c.assess(PROMPT)
    assert opener.chat_calls == 0


def test_model_missing_is_digest_mismatch():
    opener = FakeOpener([], tags={"models": [{"name": "other:1b", "digest": DIGEST}]})
    with pytest.raises(ModelUnavailable, match="digest mismatch"):
        client(opener).assess(PROMPT)
    assert opener.chat_calls == 0


def test_sha256_prefix_is_normalised():
    opener = FakeOpener([chat_body(json.dumps(GOOD))])
    assert client(opener, digest="sha256:" + DIGEST).assess(PROMPT).verdict == "upgrade"


def test_digest_checked_once_per_instance():
    opener = FakeOpener([chat_body(json.dumps(GOOD))] * 2)
    c = client(opener)
    c.assess(PROMPT)
    c.assess(PROMPT)
    c.check_available()
    assert opener.tags_calls == 1


def test_no_expected_digest_skips_pin_but_reports_not_ok():
    opener = FakeOpener([chat_body(json.dumps(GOOD))])
    c = client(opener, digest=None)
    assert c.check_available().up is True
    assert c.check_available().digest_ok is False
    assert c.assess(PROMPT).verdict == "upgrade"


def test_check_available_down_when_tags_unreachable():
    opener = FakeOpener([], tags=urllib.error.URLError("refused"))
    status = client(opener).check_available()
    assert status == OllamaStatus(up=False, digest_ok=False, off_loopback=False)


def test_off_loopback_listener_detected():
    calls: list[Any] = []

    def runner(argv, timeout):
        calls.append((list(argv), timeout))
        return CommandResult(
            0,
            "COMMAND PID USER FD TYPE NAME\nollama 1 u 3u IPv6 TCP *:11434 (LISTEN)\n",
            "",
        )

    status = client(FakeOpener([]), runner=runner).check_available()
    assert status.off_loopback is True and status.up and status.digest_ok
    assert calls == [(["lsof", "-nP", "-iTCP:11434", "-sTCP:LISTEN"], 10)]


def test_lan_address_listener_is_off_loopback():
    out = "ollama 1 u 3u IPv4 TCP 192.168.1.5:11434 (LISTEN)\n"
    status = client(
        FakeOpener([]), runner=lambda a, t: CommandResult(0, out, "")
    ).check_available()
    assert status.off_loopback is True


def test_loopback_listener_is_fine():
    out = (
        "ollama 1 u 3u IPv4 TCP 127.0.0.1:11434 (LISTEN)\n"
        "ollama 1 u 4u IPv6 TCP [::1]:11434 (LISTEN)\n"
    )
    status = client(
        FakeOpener([]), runner=lambda a, t: CommandResult(0, out, "")
    ).check_available()
    assert status.off_loopback is False


def test_runner_error_is_not_off_loopback_and_warns(caplog: pytest.LogCaptureFixture):
    def boom(argv, timeout):
        raise OSError("no lsof")

    with caplog.at_level(logging.WARNING):
        status = client(FakeOpener([]), runner=boom).check_available()
    assert status.off_loopback is False
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_proxy_env_ignored(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HTTP_PROXY", "http://evil.example:3128")
    monkeypatch.setenv("http_proxy", "http://evil.example:3128")
    built: list[Any] = []
    real = urllib.request.build_opener

    def spy(*handlers: Any) -> Any:
        built.extend(handlers)
        return real(*handlers)

    monkeypatch.setattr(urllib.request, "build_opener", spy)
    OllamaClient("m", None)
    proxies = [h for h in built if isinstance(h, urllib.request.ProxyHandler)]
    assert proxies and all(h.proxies == {} for h in proxies)


def test_response_body_never_logged(caplog: pytest.LogCaptureFixture):
    secret = "SEKRET-MODEL-OUTPUT"
    opener = FakeOpener([chat_body("garbage " + secret)] * 2)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ModelInvalidOutput):
            client(opener).assess(PROMPT)
        opener2 = FakeOpener([http_error(500)])
        with pytest.raises(ModelUnavailable):
            client(opener2).assess(PROMPT)
    text = caplog.text + "".join(str(r.args) for r in caplog.records)
    assert secret not in text and "BODY" not in text
    assert "SYSTEM-TEXT" not in text and "USER-TEXT" not in text


@pytest.mark.integration
def test_real_ollama_roundtrip():
    digest = os.environ.get("BREW_TRIAGE_OLLAMA_DIGEST")
    c = OllamaClient("qwen3:4b", digest)
    assert c.check_available().up
    assert isinstance(c.assess(PROMPT), RiskAssessment)
