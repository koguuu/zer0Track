"""Agent-local data contracts.

Deliberately tiny: the agent is not allowed to invent its own decision shape.
It consumes backend.models.CaseView / CheckerResult and produces an
InvestigationResult that is advisory only — see investigator.py docstring.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# The fixed, allowlisted set. Nothing outside this Literal can ever be
# selected or invoked — see tools.py's ToolRegistry, which is keyed on it.
ToolName = Literal["lookup_delegation", "lookup_checkpoint", "lookup_custody_history"]


class ToolCallRecord(StrictModel):
    """One (and at most one) tool invocation for a given investigation."""

    tool_name: ToolName
    arguments: dict[str, str]
    called_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    result_summary: str
    evidence_ids: list[str] = Field(default_factory=list)


class InvestigationResult(StrictModel):
    """Advisory output of one investigation pass over a case.

    IMPORTANT: this is never written back as the authoritative decision.
    backend.policy.evaluate_policy() remains the only function that decides
    accept/hold/escalate; this object is a recommendation + audit trail for
    the human reviewer (and later, for a real re-check endpoint once the
    backend exposes one — see the note in investigator.py).
    """

    shipment_id: str
    unresolved_check: str | None
    tool_call: ToolCallRecord | None
    reasoning: str
    unresolved_questions: list[str] = Field(default_factory=list)
    suggests_resolved: bool = False
