"""Ties together the two pieces that, until now, each worked but were never
connected to anything: run_investigation() (agent/investigator.py) and the
backend's authenticated POST /cases/{id}/recheck endpoint
(agent/recheck_client.py). This is the ONLY module that calls both, so
investigator.py and recheck_client.py each stay narrow, pure/network-free
respectively, and independently testable.

Hard rule carried over unchanged from investigator.py: suggests_resolved is
still never trusted directly, and this orchestrator does not change that.
Calling /recheck does not itself resolve anything — it only asks the
backend to re-run its OWN deterministic checks against its OWN trusted
data and return a fresh CaseView. If nothing in the trusted store actually
changed since the original evaluation (see the STATUS note in
investigator.py: today, nothing has, because there is no endpoint to write
a newly-registered delegate into the trusted manifest), the recheck will
simply reproduce the same unresolved result. That is not a bug in this
module — it is the backend correctly refusing to take the agent's word for
it.
"""
from __future__ import annotations

from dataclasses import dataclass

from backend.models import CaseView
from backend.storage import Store

from agent.audit_log import log_investigation
from agent.investigator import run_investigation
from agent.llm_client import LLMClient
from agent.models import InvestigationResult
from agent.recheck_client import RecheckUnavailable, request_recheck
from agent.tools import ToolRegistry

REASON_MAX_CHARS = 500  # RecheckRequest caps reason at 1000; stay well under it.


@dataclass
class OrchestrationResult:
    investigation: InvestigationResult
    audit_logged: bool
    recheck_requested: bool
    recheck_response: dict | None = None
    recheck_error: str | None = None


def _recheck_reason(investigation: InvestigationResult) -> str:
    # Deliberately templated, not investigation.reasoning verbatim: reasoning
    # may embed the inert, truncated notes preview (see
    # investigator._safe_notes_preview), and that untrusted text has no
    # reason to travel over this HTTP call or land in the backend's own
    # RECHECK audit entry a second time.
    tool = investigation.tool_call.tool_name if investigation.tool_call else "unknown"
    reason = (
        f"Agent investigation suggests '{investigation.unresolved_check}' may now "
        f"resolve (ran '{tool}')."
    )
    return reason[:REASON_MAX_CHARS]


def investigate_and_maybe_recheck(
    case: CaseView, store: Store, *, llm_client: LLMClient | None = None
) -> OrchestrationResult:
    """Runs one investigation pass, best-effort logs it to the audit chain,
    and — only if the investigation suggests the unresolved check may now
    resolve — asks the backend for an authenticated recheck. Never raises:
    any failure in logging or rechecking is captured in the returned
    OrchestrationResult rather than propagated, because neither step may be
    allowed to take down whatever triggered the investigation in the first
    place (that caller still has an InvestigationResult either way).

    `llm_client` is optional and purely advisory (see injection_detector.py):
    passing one or not never changes recheck_requested/audit_logged above.
    """
    investigation = run_investigation(case, ToolRegistry(store), llm_client=llm_client)

    audit_logged = False
    try:
        log_investigation(store, investigation)
        audit_logged = True
    except Exception:
        # Best-effort: a failed audit write must never block the recheck
        # below or hide the investigation result from the caller.
        audit_logged = False

    if not investigation.suggests_resolved:
        return OrchestrationResult(
            investigation=investigation, audit_logged=audit_logged, recheck_requested=False,
        )

    try:
        response = request_recheck(
            shipment_id=case.shipment_id,
            expected_case_version=case.version,
            reason=_recheck_reason(investigation),
        )
        return OrchestrationResult(
            investigation=investigation, audit_logged=audit_logged,
            recheck_requested=True, recheck_response=response,
        )
    except RecheckUnavailable as exc:
        return OrchestrationResult(
            investigation=investigation, audit_logged=audit_logged,
            recheck_requested=True, recheck_error=str(exc),
        )
