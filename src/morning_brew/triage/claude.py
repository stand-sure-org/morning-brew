"""Claude adapter (FR-052 transport, FR-054; DESIGN section 7 "S6 outcome").

Claude is the only stage that can approve a grey-area upgrade, so this client
is strict about what it lets through:

- The API key is fetched lazily, on the first ``assess`` only, and handed
  straight to the SDK client factory. It is never stored on this object,
  logged, placed in an exception message, or written to ``os.environ``.
- The request is exactly the call recorded by spike S6 (structured output via
  ``output_format``, effort via ``output_config``, server-side fallbacks); no
  sampling parameters and no forced ``tool_choice``.
- Every failure maps to ``ModelTransient`` or ``ModelInvalidOutput``. SDK
  exceptions are not chained (no ``__cause__`` or ``__context__``) and only
  their type name and status code are logged, so response bodies and request
  headers never reach logs or tracebacks.

``anthropic`` is the optional ``claude`` extra and is imported lazily.
"""

import logging
import re
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from morning_brew.domain import ModelInvalidOutput, ModelTransient, SecretUnavailable
from morning_brew.triage.prompt import Prompt
from morning_brew.triage.schemas import RiskAssessment

log = logging.getLogger(__name__)

MAX_TOKENS = 1024
CLIENT_TIMEOUT = 60
CLIENT_MAX_RETRIES = 2
BETAS = ("server-side-fallback-2026-07-01",)
AUTH = "auth"
SECRET = "secret"
_CATEGORY = re.compile(r"[a-z0-9_-]{1,40}")
# A finished answer; None is tolerated for an SDK parse result that omits it.
_ACCEPTED_STOPS = frozenset({"end_turn", None})


def default_client_factory(key: str) -> Any:
    import anthropic

    return anthropic.Anthropic(
        api_key=key, max_retries=CLIENT_MAX_RETRIES, timeout=CLIENT_TIMEOUT
    )


def _refusal_category(response: Any) -> str:
    category = getattr(getattr(response, "stop_details", None), "category", None)
    if isinstance(category, str) and _CATEGORY.fullmatch(category):
        return category
    return "unknown"


class ClaudeClient:
    """``ModelClient`` backed by the Anthropic API.

    ``last_error_kind`` is ``"auth"`` once any call in this client's lifetime
    (one run) failed authentication or permission, or ``"secret"`` when the
    key could not be fetched; the CLI surfaces either as an urgent anomaly so
    a stale or missing key is noticed rather than holding forever.
    """

    def __init__(
        self,
        model: str,
        effort: str,
        key_provider: Callable[[], str],
        client_factory: Callable[[str], Any] | None = None,
    ):
        self.model = model
        self.effort = effort
        self._key_provider = key_provider
        self._factory = client_factory or default_client_factory
        self._client: Any = None
        self._setup_failure: str | None = None
        self.last_error_kind: str | None = None

    def __repr__(self) -> str:
        return f"ClaudeClient(model={self.model!r}, effort={self.effort!r})"

    # client ----------------------------------------------------------------

    def _get_client(self) -> Any:
        """Build the SDK client once; a failed build is not retried this run."""
        if self._client is not None:
            return self._client
        if self._setup_failure is not None:
            raise ModelTransient(self._setup_failure)
        try:
            self._client = self._factory(self._key_provider())
        except SecretUnavailable:
            log.warning("claude key unavailable")
            self._setup_failure = SECRET
            self.last_error_kind = SECRET
        except Exception as exc:  # noqa: BLE001 - mapped below, type name only
            log.warning("claude client setup failed: %s", type(exc).__name__)
            self._setup_failure = "claude error"
        if self._setup_failure is not None:
            raise ModelTransient(self._setup_failure)
        return self._client

    # public ----------------------------------------------------------------

    def assess(self, prompt: Prompt) -> RiskAssessment:
        client = self._get_client()
        # The mapped error is raised outside ``except`` so it carries neither
        # ``__cause__`` nor ``__context__``: an SDK exception holds the request,
        # whose headers carry the key.
        failure: Exception | None = None
        try:
            response = client.beta.messages.parse(
                model=self.model,
                max_tokens=MAX_TOKENS,
                system=prompt.system,
                messages=[{"role": "user", "content": prompt.user}],
                output_config={"effort": self.effort},
                output_format=RiskAssessment,
                betas=list(BETAS),
                fallbacks="default",
            )
        except ValidationError:
            log.warning("claude output failed schema validation")
            failure = ModelInvalidOutput("claude output failed schema validation")
        except Exception as exc:  # noqa: BLE001 - every failure is mapped
            failure = self._map_error(exc)
        if failure is not None:
            raise failure
        return self._parse(response)

    # response ----------------------------------------------------------------

    @staticmethod
    def _parse(response: Any) -> RiskAssessment:
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            category = _refusal_category(response)
            log.warning("claude refused (%s)", category)
            raise ModelTransient(f"refusal:{category}")
        if stop == "max_tokens":
            raise ModelInvalidOutput("claude output truncated (max_tokens)")
        if stop not in _ACCEPTED_STOPS:  # [M3] allowlist, value never echoed
            log.warning("claude stopped unexpectedly")
            raise ModelInvalidOutput("claude stopped for an unexpected reason")
        obj = getattr(response, "parsed_output", None)
        if obj is None:
            raise ModelInvalidOutput("claude returned no parsed output")
        if not isinstance(obj, RiskAssessment):
            raise ModelInvalidOutput(f"claude output is {type(obj).__name__}")
        try:
            return RiskAssessment.model_validate(
                obj.model_dump(warnings=False), strict=True
            )
        except (ValidationError, AttributeError, TypeError, ValueError):
            pass
        raise ModelInvalidOutput("claude output failed schema validation")

    # errors ------------------------------------------------------------------

    def _map_error(self, exc: Exception) -> Exception:
        kind = type(exc).__name__
        try:
            import anthropic
        except ImportError:  # pragma: no cover - the extra builds the client
            log.warning("claude call failed: %s", kind)
            return ModelTransient("claude error")
        status = getattr(exc, "status_code", None)
        log.warning("claude call failed: %s (status %s)", kind, status)
        if isinstance(exc, anthropic.RateLimitError):
            return ModelTransient("rate limit")
        if isinstance(exc, anthropic.APITimeoutError):
            return ModelTransient("timeout")
        if isinstance(exc, anthropic.APIConnectionError):
            return ModelTransient("connection")
        if isinstance(exc, anthropic.InternalServerError):
            return ModelTransient(f"server error {status}")
        if isinstance(
            exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)
        ):
            self.last_error_kind = AUTH
            return ModelTransient(AUTH)
        if isinstance(exc, anthropic.APIStatusError):
            if isinstance(status, int) and status >= 500:
                return ModelTransient(f"server error {status}")
            return ModelInvalidOutput(f"claude rejected request: HTTP {status}")
        return ModelTransient("claude error")
