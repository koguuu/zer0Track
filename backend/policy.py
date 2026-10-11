"""Deterministic policy. No model output can override a failed mandatory check."""
from __future__ import annotations

from backend.models import AgentDecision, CheckerResult, Status

REQUIRED = ("parcel_code_integrity", "identity_authority", "location")


def evaluate_policy(checks: list[CheckerResult]) -> AgentDecision:
    by_name = {x.checker_name: x for x in checks}
    if len(checks) != len(by_name):
        raise ValueError("Duplicate checker result")
    if set(by_name) != set(REQUIRED):
        raise ValueError("Missing or unexpected mandatory checker result")
    states = {name: by_name[name].status for name in REQUIRED}
    unresolved = [name for name, status in states.items() if status != Status.PASS]
    refs = list(dict.fromkeys(ref for check in checks for ref in check.evidence_ids))
    base = {"unresolved_checks": unresolved, "evidence_ids": refs}
    if states["parcel_code_integrity"] == Status.FAIL:
        return AgentDecision(decision="ESCALATE", recommended_action="fraud_review",
            requires_human_approval=True, reason="Parcel integrity/code failed; cannot accept", **base)
    if states["identity_authority"] == Status.FAIL:
        return AgentDecision(decision="RECOMMEND_HOLD", recommended_action="hold",
            requires_human_approval=True, reason="Trusted identity or receiver authority failed", **base)
    if all(state == Status.PASS for state in states.values()):
        return AgentDecision(decision="ACCEPT_READY", recommended_action="accept",
            requires_human_approval=False, reason="All three mandatory checks passed; revalidate at commit", **base)
    return AgentDecision(decision="REQUEST_VERIFICATION", recommended_action="verify",
        requires_human_approval=False, reason="Mandatory check failed/unknown; gather valid evidence or review",
        **base)
