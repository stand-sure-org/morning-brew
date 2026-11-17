"""Triage chain for GREY members (FR-050, 051, 052, 053, 056; DESIGN section 6-7).

Order: the deterministic suspicious-text flag (hold, no prompt built, no
model call), then the optional local model as a recorded second opinion
only, then Claude judging blind from its own prompt. Only Claude, through
``accepts_upgrade``, can produce an upgrade; shadow mode turns that upgrade
into a transient hold until the ship gate passes.
"""

import logging
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from morning_brew.domain import (
    ModelInvalidOutput,
    ModelTransient,
    ModelUnavailable,
    TriageOutcome,
    TriageRequest,
)
from morning_brew.ports import TriagePort
from morning_brew.triage.coverage import accepts_upgrade, coverage_ok
from morning_brew.triage.prompt import Prompt, build_prompt
from morning_brew.triage.schemas import RiskAssessment
from morning_brew.triage.suspicious import suspicious_text

log = logging.getLogger(__name__)

Mode = Literal["shadow", "live"]
_MODES = frozenset({"shadow", "live"})
_RISK_FIELDS = (
    "mentions_migration",
    "mentions_data_format",
    "mentions_restart",
    "mentions_manual_step",
)

STAGE_SUSPICIOUS = "suspicious"
STAGE_CLAUDE = "claude"


class ModelClient(Protocol):
    def assess(self, prompt: Prompt) -> RiskAssessment:
        """Raises ModelUnavailable | ModelInvalidOutput | ModelTransient."""
        ...


def _validated(result: Any) -> RiskAssessment:
    """Re-validate a model's answer as the pydantic schema.

    An instance is dumped and validated again so an object built with
    ``model_construct`` (missing or mistyped fields) is not trusted.
    """
    try:
        if isinstance(result, RiskAssessment):
            result = result.model_dump(warnings=False)
        if not isinstance(result, dict):
            raise ModelInvalidOutput(f"not a risk assessment: {type(result).__name__}")
        return RiskAssessment.model_validate(result, strict=True)
    except ModelInvalidOutput:
        raise
    except (ValidationError, AttributeError, TypeError, ValueError) as exc:
        raise ModelInvalidOutput("risk assessment failed validation") from exc


def _flagged(a: RiskAssessment) -> bool:
    return any(getattr(a, f) for f in _RISK_FIELDS)


class TriageChain(TriagePort):
    def __init__(self, local: ModelClient | None, claude: ModelClient, mode: Mode):
        if mode not in _MODES:
            raise ValueError(f"unknown chain mode: {mode!r}")
        self._local = local
        self._claude = claude
        self._mode = mode

    def triage(self, request: TriageRequest) -> TriageOutcome:
        name = request.full_name
        # C10: flag before any prompt is built or any model is called.
        if suspicious_text(request.caveats):
            log.info("triage %s: decided by stage=suspicious (hold)", name)
            return TriageOutcome("hold", "suspicious caveat text", STAGE_SUSPICIOUS)

        local_flagged = self._local_opinion(request)

        # Blind: Claude's prompt comes from the request alone, fresh token.
        try:
            a = _validated(self._claude.assess(build_prompt(request)))
        except ModelInvalidOutput:
            log.warning("triage %s: claude returned invalid output (transient)", name)
            return self._transient("invalid output", local_flagged)
        except (ModelTransient, ModelUnavailable) as exc:
            # Type only: exception text from an SDK adapter may carry secrets.
            log.warning(
                "triage %s: claude unavailable (transient): %s",
                name,
                type(exc).__name__,
            )
            return self._transient(
                f"claude unavailable: {type(exc).__name__}", local_flagged
            )
        except Exception as exc:
            log.warning(
                "triage %s: unexpected claude error (transient): %s",
                name,
                type(exc).__name__,
            )
            return self._transient("claude error", local_flagged)

        upgrade = accepts_upgrade(request.caveats, a)
        disagreement = local_flagged and upgrade
        if upgrade:
            if self._mode == "shadow":
                log.info(
                    "triage %s: decided by stage=claude (shadow: would upgrade)", name
                )
                return TriageOutcome(
                    "transient",
                    "shadow",
                    STAGE_CLAUDE,
                    local_flagged=local_flagged,
                    disagreement=disagreement,
                    would_be="upgrade",
                    reasoning=a.rationale,
                )
            log.info("triage %s: decided by stage=claude (upgrade)", name)
            return TriageOutcome(
                "upgrade",
                "claude cleared",
                STAGE_CLAUDE,
                local_flagged=local_flagged,
                disagreement=disagreement,
                reasoning=a.rationale,
            )

        if a.verdict != "upgrade":
            reason = "claude hold"
        elif not coverage_ok(request.caveats, a):
            reason = "coverage check failed"
        else:
            reason = "risk flagged"
        log.info("triage %s: decided by stage=claude (hold: %s)", name, reason)
        return TriageOutcome(
            "hold",
            reason,
            STAGE_CLAUDE,
            local_flagged=local_flagged,
            disagreement=False,
            would_be="hold" if self._mode == "shadow" else None,
            reasoning=a.rationale,
        )

    def _local_opinion(self, request: TriageRequest) -> bool:
        """The local model's flag, recorded only. Never decides; never raises."""
        if self._local is None:
            return False
        try:
            a = _validated(self._local.assess(build_prompt(request)))
        except Exception as exc:
            log.info(
                "triage %s: local stage skipped (%s)",
                request.full_name,
                type(exc).__name__,
            )
            return False
        return _flagged(a)

    def _transient(self, reason: str, local_flagged: bool) -> TriageOutcome:
        return TriageOutcome(
            "transient", reason, STAGE_CLAUDE, local_flagged=local_flagged
        )
