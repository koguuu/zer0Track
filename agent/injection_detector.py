"""Runs an LLM-based classifier over a case's untrusted notes to produce an
advisory InjectionSignal for the human operator and the audit log -- and
nothing else.

THE ISOLATION GUARANTEE (enforced by injection_detector_tests.py's
test_injection_signal_never_changes_investigation_outcome): this module's
output is never read by agent.investigator's tool-selection logic, never
read by backend.policy, and never changes `suggests_resolved`. It exists
alongside the deterministic investigation, not inside its decision path.
Concretely: run_investigation() in investigator.py calls this function
(when an llm_client is supplied) purely to populate
InvestigationResult.injection_signal for display/audit; deleting that one
assignment line would not change a single other line's behavior, and the
isolation test proves that by running the full investigation twice with a
classifier forced to return opposite verdicts and asserting every other
field of the result is byte-identical.

Why this is the "effective use of Agentic AI" piece of the project, not
just decoration: the deterministic investigator already does real
agentic-style reasoning (select a tool, interpret read-only evidence,
decide what's resolved) -- but entirely without touching untrusted text.
This module is the one place an LLM actually reads adversarial,
attacker-controlled input and makes a judgment call about it. Keeping that
judgment call confined to an annotation that a human reviews, rather than
letting it drive any action, is deliberately the whole design: an LLM
spending its agency on detecting manipulation, with zero ability to BE
manipulated into changing an outcome, because it was never wired to one.

FAIL-OPEN BY DESIGN, not fail-closed: if the classifier is unavailable,
errors, or times out, the investigation proceeds exactly as it would with
no classifier configured at all (injection_signal=None). This is correct
specifically because the signal is advisory-only -- the mandatory checks,
tool selection, and policy decision never depended on it, so losing it
loses an annotation, not a safeguard. Contrast with tools.py's
ToolTimeoutError, which IS fail-closed (routes to operator review),
because that failure is on the path that produces the actual decision.
"""
from __future__ import annotations

import concurrent.futures

from agent.llm_client import LLM_CLASSIFY_TIMEOUT_SECONDS, LLMClient, LLMUnavailable
from agent.models import InjectionSignal

_CLASSIFIER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="zer0track-injection-classify"
)


def detect_injection_signal(
    notes: str | None, llm_client: LLMClient | None, *, timeout_seconds: float = LLM_CLASSIFY_TIMEOUT_SECONDS
) -> InjectionSignal | None:
    """Returns an InjectionSignal, or None when there's nothing to classify
    (no notes, no client configured) or the classifier failed/timed out.
    Never raises -- a classifier problem must never surface as an
    investigation failure, since nothing downstream of this treats a
    missing signal as a safeguard gap. See module docstring.
    """
    if not notes or llm_client is None:
        return None

    future = _CLASSIFIER_EXECUTOR.submit(llm_client.classify_injection, notes=notes, timeout_seconds=timeout_seconds)
    try:
        raw = future.result(timeout=timeout_seconds)
    except Exception:  # noqa: BLE001 - timeout, LLMUnavailable, or any other failure: fail-open, see docstring
        return None

    try:
        return InjectionSignal.model_validate(raw)
    except Exception:  # noqa: BLE001 - a malformed response degrades the same as an unavailable one
        return None
