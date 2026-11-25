"""Structured model output for caveat triage (FR-052).

Field order is part of the contract: the risk booleans come first so a model
commits to what the caveat mentions before it writes a verdict.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class RiskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentions_migration: bool
    mentions_data_format: bool
    mentions_restart: bool
    mentions_manual_step: bool
    boilerplate_only: bool
    verdict: Literal["upgrade", "hold"]
    rationale: str
