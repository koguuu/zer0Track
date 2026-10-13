"""Single entry point for "investigate this case now".

Exists so the backend's POST /cases/{id}/investigate endpoint stays a
three-line wrapper and the real behavior can be unit tested without FastAPI.
Nothing here decides anything: it loads the case the backend already
evaluated, runs agent.orchestrator.investigate_and_maybe_recheck(), and
returns a plain dict. The investigation is advisory and is written to the
audit chain as an AGENT_INVESTIGATION entry (see agent/audit_log.py), which
is what GET /cases/{id}/audit then shows on the dashboard timeline.

Layering note: agent.* imports backend.*, so backend/api.py must import this
module lazily inside the handler, never at module load time.
"""
from __future__ import annotations

import os

from backend.storage import Store

from agent.llm_client import AnthropicInjectionClassifier, GroqInjectionClassifier, LLMClient
from agent.orchestrator import investigate_and_maybe_recheck


def default_llm_client() -> LLMClient | None:
    """Same provider choice as evaluation/kogu_adapter.py: an Anthropic key
    selects Anthropic, GROQ_API_KEY selects Groq, otherwise no classifier
    (injection_signal stays None and nothing else changes).
    """
    if os.environ.get("ZTRACK_LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicInjectionClassifier()
    if os.environ.get("GROQ_API_KEY"):
        return GroqInjectionClassifier()
    return None


def investigate_case(store: Store, shipment_id: str, *, use_env_llm: bool = True) -> dict:
    """Runs one investigation pass for an existing case.

    Raises backend.storage.BackendError(404) when the case does not exist, so
    an unknown shipment never writes anything to the audit chain. Otherwise
    never raises for investigation-side problems; they are reported in the
    returned dict (see orchestrator.investigate_and_maybe_recheck).
    """
    case = store.get_case(shipment_id)
    outcome = investigate_and_maybe_recheck(
        case, store, llm_client=default_llm_client() if use_env_llm else None
    )
    recheck_case_version = None
    if isinstance(outcome.recheck_response, dict):
        recheck_case_version = outcome.recheck_response.get("version")
    return {
        "shipment_id": shipment_id,
        "case_version": case.version,
        "audit_logged": outcome.audit_logged,
        "recheck_requested": outcome.recheck_requested,
        "recheck_error": outcome.recheck_error,
        "recheck_case_version": recheck_case_version,
        "investigation": outcome.investigation.model_dump(mode="json"),
    }
