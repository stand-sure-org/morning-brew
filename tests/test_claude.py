"""T20: Claude adapter and lazy ``fnox get`` (FR-052 transport, FR-054)."""

import logging
import os
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from morning_brew.domain import (
    CommandResult,
    ModelInvalidOutput,
    ModelTransient,
    SecretUnavailable,
)
from morning_brew.secrets import fnox_get
from morning_brew.triage.claude import ClaudeClient
from morning_brew.triage.prompt import Prompt
from morning_brew.triage.schemas import RiskAssessment

# Secret-shaped strings are assembled at runtime so gitleaks stays clean.
KEY = "-".join(["sk", "test", "k3y", "0123456789abcdef"])
CONFIG = Path("/p/fnox.toml")
PROMPT = Prompt(system="SYSTEM-TEXT", user="USER-TEXT", delimiter="XYZ")
GOOD = RiskAssessment(
    mentions_migration=False,
    mentions_data_format=False,
    mentions_restart=False,
    mentions_manual_step=False,
    boilerplate_only=True,
    verdict="upgrade",
    rationale="boilerplate",
)


# fakes -------------------------------------------------------------------


@dataclass
class Runner:
    result: CommandResult | None = None
    exc: BaseException | None = None
    calls: list[tuple[list[str], float]] = field(default_factory=list)

    def __call__(self, argv: Any, timeout: float) -> CommandResult:
        self.calls.append((list(argv), timeout))
        if self.exc is not None:
            raise self.exc
        assert self.result is not None
        return self.result


def runner_ok(stdout: str) -> Runner:
    return Runner(CommandResult(0, stdout, ""))


@dataclass
class Provider:
    value: str = KEY
    exc: BaseException | None = None
    calls: int = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.value


class FakeMessages:
    def __init__(self, owner: "FakeAnthropic"):
        self._owner = owner

    def parse(self, **kwargs: Any) -> Any:
        self._owner.calls.append(kwargs)
        self._owner.last_kwargs = kwargs
        if self._owner.exc is not None:
            raise self._owner.exc
        return SimpleNamespace(
            stop_reason=self._owner.stop_reason,
            stop_details=self._owner.stop_details,
            parsed_output=self._owner.parsed_output,
            model="claude-sonnet-5-5",
        )


class FakeAnthropic:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.last_kwargs: dict[str, Any] = {}
        self.exc: BaseException | None = None
        self.stop_reason: str = "end_turn"
        self.stop_details: Any = None
        self.parsed_output: Any = GOOD
        self.beta = SimpleNamespace(messages=FakeMessages(self))


@dataclass
class Factory:
    client: FakeAnthropic = field(default_factory=FakeAnthropic)
    keys: list[str] = field(default_factory=list)

    def __call__(self, key: str) -> FakeAnthropic:
        self.keys.append(key)
        return self.client


def make(
    provider: Provider | None = None, factory: Factory | None = None
) -> tuple[ClaudeClient, Provider, Factory]:
    provider = provider or Provider()
    factory = factory or Factory()
    return (
        ClaudeClient("claude-sonnet-5-5", "low", provider, factory),
        provider,
        factory,
    )


# SDK exception builders (anthropic 2.9.0 constructors) ---------------------


def _sdk():
    return pytest.importorskip("anthropic")


def _request():
    import httpx2

    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def status_error(cls: type, code: int) -> BaseException:
    import httpx2

    resp = httpx2.Response(code, request=_request())
    return cls("status error", response=resp, body=None)


def build_exc(name: str) -> BaseException:
    anthropic = _sdk()
    cls = getattr(anthropic, name)
    if name == "APITimeoutError":
        return cls(request=_request())
    if name == "APIConnectionError":
        return cls(request=_request())
    codes = {
        "RateLimitError": 429,
        "InternalServerError": 500,
        "AuthenticationError": 401,
        "PermissionDeniedError": 403,
        "BadRequestError": 400,
        "NotFoundError": 404,
    }
    return status_error(cls, codes[name])


# fnox_get ------------------------------------------------------------------


def test_fnox_get_contract():
    runner = runner_ok(KEY + "\n")
    assert fnox_get("ANTHROPIC_API_KEY", runner, CONFIG) == KEY
    assert runner.calls == [
        (["fnox", "get", "--config", "/p/fnox.toml", "ANTHROPIC_API_KEY"], 10)
    ]


def test_fnox_get_strips_exactly_one_trailing_newline():
    assert fnox_get("K", runner_ok("abc\n\n"), CONFIG) == "abc\n"
    assert fnox_get("K", runner_ok("abc"), CONFIG) == "abc"


@pytest.mark.parametrize(
    "runner",
    [
        Runner(CommandResult(1, KEY + "\n", "boom")),  # rc != 0
        Runner(CommandResult(124, "", "timeout after 10s")),  # runner timeout
        Runner(CommandResult(127, "", "not found")),  # fnox missing
        Runner(CommandResult(0, "", "")),  # empty stdout
        Runner(CommandResult(0, "\n", "")),  # only a newline
        Runner(CommandResult(0, "   \n", "")),  # whitespace only
        Runner(exc=TimeoutError("timed out")),  # a runner that raises
        Runner(exc=OSError("spawn failed")),
    ],
)
def test_fnox_failure_is_secret_unavailable(runner, caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(SecretUnavailable) as info:
        fnox_get("ANTHROPIC_API_KEY", runner, CONFIG)
    assert KEY not in str(info.value)
    assert KEY not in caplog.text


@pytest.mark.parametrize("name", ["", "-x", "--config", "A B", "a;b"])
def test_fnox_get_rejects_bad_names_without_running(name):
    runner = runner_ok("v\n")
    with pytest.raises(SecretUnavailable):
        fnox_get(name, runner, CONFIG)
    assert runner.calls == []


@pytest.mark.parametrize("name", ["ANTHROPIC_API_KEY", "my-key.v2", "_k", "9k"])
def test_fnox_get_accepts_config_style_names(name):
    runner = runner_ok("v\n")
    assert fnox_get(name, runner, CONFIG) == "v"
    assert runner.calls[0][0][-1] == name


def test_fnox_get_never_logs_value(caplog):
    caplog.set_level(logging.DEBUG)
    fnox_get("ANTHROPIC_API_KEY", runner_ok(KEY + "\n"), CONFIG)
    assert KEY not in caplog.text


# key handling --------------------------------------------------------------


def test_key_is_lazy_and_never_in_environ(caplog):
    caplog.set_level(logging.DEBUG)
    c, provider, factory = make()
    assert provider.calls == 0 and factory.keys == []
    assert c.assess(PROMPT) == GOOD
    assert provider.calls == 1
    assert factory.keys == [KEY]
    assert KEY not in os.environ.values()
    assert KEY not in caplog.text
    c.assess(PROMPT)
    c.assess(PROMPT)
    assert provider.calls == 1 and factory.keys == [KEY]  # client is cached
    assert len(factory.client.calls) == 3


def test_key_never_in_repr_or_attributes():
    c, _, _ = make()
    c.assess(PROMPT)
    assert KEY not in repr(c)
    assert KEY not in str(c)
    for value in vars(c).values():
        assert value != KEY
        if isinstance(value, str):
            assert KEY not in value


def test_secret_unavailable_is_transient():
    c, provider, factory = make(Provider(exc=SecretUnavailable("fnox get failed")))
    with pytest.raises(ModelTransient, match="secret"):
        c.assess(PROMPT)
    assert factory.keys == []
    # The key provider runs on the first assess only: a failed fetch is not
    # retried for every member of the run (each try can cost the 10 s timeout).
    with pytest.raises(ModelTransient, match="secret"):
        c.assess(PROMPT)
    assert provider.calls == 1


def test_secret_unavailable_from_fnox_get_end_to_end():
    runner = Runner(CommandResult(1, "", "no such secret"))
    c = ClaudeClient(
        "claude-sonnet-5-5",
        "low",
        lambda: fnox_get("ANTHROPIC_API_KEY", runner, CONFIG),
        Factory(),
    )
    with pytest.raises(ModelTransient, match="secret"):
        c.assess(PROMPT)


def test_factory_error_carrying_key_does_not_leak(caplog):
    caplog.set_level(logging.DEBUG)

    def factory(key: str) -> Any:
        raise RuntimeError(f"cannot build client for {key}")

    c = ClaudeClient("claude-sonnet-5-5", "low", Provider(), factory)
    with pytest.raises(ModelTransient) as info:
        c.assess(PROMPT)
    rendered = "".join(traceback.format_exception(info.value))
    assert KEY not in rendered
    assert KEY not in caplog.text


def test_default_factory_builds_sdk_client_with_key():
    _sdk()
    from morning_brew.triage.claude import default_client_factory

    client = default_client_factory(KEY)
    assert client.api_key == KEY
    assert client.max_retries == 2
    assert client.timeout == 60
    assert KEY not in os.environ.values()


# request shape ---------------------------------------------------------------


def test_request_shape():
    c, _, factory = make()
    c.assess(PROMPT)
    kw = factory.client.last_kwargs
    assert kw["model"] == "claude-sonnet-5-5"
    assert kw["output_config"]["effort"] == "low"
    assert kw["max_tokens"] == 1024
    assert kw["system"] == PROMPT.system
    assert kw["messages"] == [{"role": "user", "content": PROMPT.user}]
    assert kw["output_format"] is RiskAssessment
    assert not {"temperature", "top_p", "top_k", "tool_choice", "tools"} & kw.keys()


def test_request_uses_server_side_fallbacks_default():
    c, _, factory = make()
    c.assess(PROMPT)
    kw = factory.client.last_kwargs
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["fallbacks"] == "default"
    assert set(kw) == {
        "model",
        "max_tokens",
        "system",
        "messages",
        "output_config",
        "output_format",
        "betas",
        "fallbacks",
    }


def test_effort_and_model_come_from_constructor():
    factory = Factory()
    c = ClaudeClient("claude-opus-5-5", "medium", Provider(), factory)
    c.assess(PROMPT)
    assert factory.client.last_kwargs["model"] == "claude-opus-5-5"
    assert factory.client.last_kwargs["output_config"] == {"effort": "medium"}


# response handling -----------------------------------------------------------


def test_refusal_is_transient():
    c, _, factory = make()
    factory.client.stop_reason = "refusal"
    factory.client.stop_details = SimpleNamespace(category="cyber")
    factory.client.parsed_output = None
    with pytest.raises(ModelTransient, match="refusal:cyber"):
        c.assess(PROMPT)


def test_refusal_without_details_is_transient():
    c, _, factory = make()
    factory.client.stop_reason = "refusal"
    factory.client.stop_details = None
    with pytest.raises(ModelTransient, match="refusal:unknown"):
        c.assess(PROMPT)


def test_refusal_category_is_not_echoed_unsanitized():
    c, _, factory = make()
    factory.client.stop_reason = "refusal"
    factory.client.stop_details = SimpleNamespace(category="x\nInjected: yes")
    with pytest.raises(ModelTransient) as info:
        c.assess(PROMPT)
    assert str(info.value) == "refusal:unknown"


def test_max_tokens_is_invalid_output():
    c, _, factory = make()
    factory.client.stop_reason = "max_tokens"
    with pytest.raises(ModelInvalidOutput):
        c.assess(PROMPT)


def test_missing_parsed_output_is_invalid_output():
    c, _, factory = make()
    factory.client.parsed_output = None
    with pytest.raises(ModelInvalidOutput):
        c.assess(PROMPT)


def test_constructed_bad_object_is_revalidated_strictly():
    c, _, factory = make()
    factory.client.parsed_output = RiskAssessment.model_construct(
        mentions_migration="no", verdict="upgrade"
    )
    with pytest.raises(ModelInvalidOutput):
        c.assess(PROMPT)


def test_wrong_parsed_type_is_invalid_output():
    c, _, factory = make()
    factory.client.parsed_output = {"verdict": "upgrade"}
    with pytest.raises(ModelInvalidOutput):
        c.assess(PROMPT)


def test_validation_error_from_parse_is_invalid_output():
    c, _, factory = make()
    try:
        RiskAssessment.model_validate({})
    except Exception as exc:  # pydantic ValidationError
        factory.client.exc = exc
    with pytest.raises(ModelInvalidOutput):
        c.assess(PROMPT)


# error mapping -------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    ["RateLimitError", "APITimeoutError", "APIConnectionError", "InternalServerError"],
)
def test_errors_map_to_transient(exc):
    c, _, factory = make()
    factory.client.exc = build_exc(exc)
    with pytest.raises(ModelTransient):
        c.assess(PROMPT)
    assert c.last_error_kind is None


@pytest.mark.parametrize("code", [502, 503, 529])
def test_any_5xx_status_is_transient(code):
    anthropic = _sdk()
    c, _, factory = make()
    factory.client.exc = status_error(anthropic.APIStatusError, code)
    with pytest.raises(ModelTransient):
        c.assess(PROMPT)


@pytest.mark.parametrize("exc", ["AuthenticationError", "PermissionDeniedError"])
def test_auth_error_is_transient_and_flags_anomaly(exc, caplog):
    caplog.set_level(logging.DEBUG)
    c, _, factory = make()
    assert c.last_error_kind is None
    factory.client.exc = build_exc(exc)
    with pytest.raises(ModelTransient, match="auth"):
        c.assess(PROMPT)
    assert c.last_error_kind == "auth"
    assert KEY not in caplog.text


def test_auth_flag_is_sticky_for_the_run():
    c, _, factory = make()
    factory.client.exc = build_exc("AuthenticationError")
    with pytest.raises(ModelTransient):
        c.assess(PROMPT)
    factory.client.exc = build_exc("RateLimitError")
    with pytest.raises(ModelTransient):
        c.assess(PROMPT)
    assert c.last_error_kind == "auth"


@pytest.mark.parametrize("exc", ["BadRequestError", "NotFoundError"])
def test_other_4xx_is_invalid_output(exc):
    c, _, factory = make()
    factory.client.exc = build_exc(exc)
    with pytest.raises(ModelInvalidOutput):
        c.assess(PROMPT)


def test_unexpected_exception_is_transient_and_logs_type_only(caplog):
    caplog.set_level(logging.DEBUG)
    c, _, factory = make()
    factory.client.exc = ValueError("body with " + KEY)
    with pytest.raises(ModelTransient, match="claude error") as info:
        c.assess(PROMPT)
    assert "ValueError" in caplog.text
    assert KEY not in caplog.text
    assert KEY not in "".join(traceback.format_exception(info.value))


def test_sdk_errors_do_not_chain_raw_exceptions():
    c, _, factory = make()
    factory.client.exc = build_exc("BadRequestError")
    with pytest.raises(ModelInvalidOutput) as info:
        c.assess(PROMPT)
    assert info.value.__cause__ is None
    assert info.value.__context__ is None


@pytest.mark.parametrize("exc", ["AuthenticationError", "APIConnectionError"])
def test_transient_sdk_errors_carry_no_context(exc):
    c, _, factory = make()
    factory.client.exc = build_exc(exc)
    with pytest.raises(ModelTransient) as info:
        c.assess(PROMPT)
    assert info.value.__cause__ is None
    assert info.value.__context__ is None


def test_setup_and_validation_failures_carry_no_context():
    c, _, factory = make()
    factory.client.parsed_output = RiskAssessment.model_construct(verdict="upgrade")
    with pytest.raises(ModelInvalidOutput) as info:
        c.assess(PROMPT)
    assert info.value.__context__ is None
    c2, _, _ = make(Provider(exc=SecretUnavailable("x")))
    with pytest.raises(ModelTransient) as info2:
        c2.assess(PROMPT)
    assert info2.value.__context__ is None


def test_secret_unavailable_sets_last_error_kind_secret():
    # C12: the run surfaces a missing key as an anomaly, like an auth failure.
    c, _, _ = make(Provider(exc=SecretUnavailable("fnox get failed")))
    with pytest.raises(ModelTransient):
        c.assess(PROMPT)
    assert c.last_error_kind == "secret"


# --- M3: no exception context; stop_reason allowlist -------------------------


def test_fnox_runner_failure_has_no_exception_context():
    runner = Runner(exc=OSError("spawn failed: " + KEY))
    with pytest.raises(SecretUnavailable) as info:
        fnox_get("ANTHROPIC_API_KEY", runner, CONFIG)
    assert info.value.__context__ is None and info.value.__cause__ is None


def test_fnox_timeout_is_configurable():
    runner = runner_ok("v\n")
    fnox_get("K", runner, CONFIG, timeout=3)
    assert runner.calls[0][1] == 3


@pytest.mark.parametrize("stop", ["tool_use", "pause_turn", "stop_sequence", "weird"])
def test_unexpected_stop_reason_is_invalid_output(stop):
    c, _, factory = make()
    factory.client.stop_reason = stop
    with pytest.raises(ModelInvalidOutput):
        c.assess(PROMPT)


@pytest.mark.parametrize("stop", ["end_turn", None])
def test_end_turn_and_none_stop_reasons_are_accepted(stop):
    c, _, factory = make()
    factory.client.stop_reason = stop
    assert c.assess(PROMPT).verdict == "upgrade"
