"""Backend for the live "try your own injection attack" demo.

Purpose: let a judge/reviewer type arbitrary attack text (a fake supervisor
approval, a forged "SYSTEM:" instruction, anything) and watch two things
side by side in one response:
  1. What the advisory LLM classifier makes of that exact text, right now.
  2. The REAL, already-computed decision for a real case -- read verbatim
     from storage, completely untouched by step 1.

This is the isolation guarantee (see docs/THREAT_MODEL.md) made literally
interactive instead of only provable by reading a test file. It is
read-only and side-effect-free by construction:
  - No ShipmentEvent is created, no case is evaluated or mutated.
  - Nothing here is written to the audit chain (contrast with
    agent/service.py's investigate_case, which deliberately IS audited --
    a judge poking at the sandbox should not pollute a real case's audit
    trail with "testing" the live demo).
  - case_decision below is read straight from backend.storage.Store, the
    exact same object GET /cases/{id} would return -- there is no code
    path by which classifier_result below could have influenced it, because
    this function computes classifier_result AFTER already reading
    case_decision from storage, and never writes it back anywhere.
"""
from __future__ import annotations

from backend.storage import BackendError, Store

from agent.redaction import redact_digit_sequences
from agent.injection_detector import detect_injection_signal
from agent.service import default_llm_client

SANDBOX_TEXT_MAX_CHARS = 2000


def run_injection_sandbox(store: Store, shipment_id: str, text: str) -> dict:
    """Raises BackendError(404) if shipment_id doesn't exist (same as any
    other /cases/{id} read) and BackendError(400) if text is empty or over
    the length cap -- both surfaced to the caller as ordinary HTTP errors
    by backend/api.py, same pattern as every other endpoint here.
    """
    if not text or not text.strip():
        raise BackendError(400, "text must be non-empty")
    if len(text) > SANDBOX_TEXT_MAX_CHARS:
        raise BackendError(400, f"text must be at most {SANDBOX_TEXT_MAX_CHARS} characters")

    # Read the REAL case first, in full, before touching the classifier at
    # all -- this ordering is not cosmetic: it's what makes "the decision
    # below was read before your text was even classified" a true
    # statement the response itself can make.
    case = store.get_case(shipment_id)

    redacted = redact_digit_sequences(text)
    signal = detect_injection_signal(redacted, default_llm_client())

    return {
        "shipment_id": shipment_id,
        "submitted_text_redacted": redacted,
        "classifier_result": signal.model_dump(mode="json") if signal is not None else None,
        "classifier_available": signal is not None,
        "case_decision": case.decision.model_dump(mode="json"),
        "case_version": case.version,
        "isolation_note": (
            "case_decision above was read from storage before your text was classified, "
            "and is never written back to. Submitting different text changes "
            "classifier_result only -- case_decision will be identical every time, no "
            "matter what you type here, because backend/policy.py never reads this "
            "endpoint's input at all."
        ),
    }
