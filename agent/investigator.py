"""Investigates ONE unresolved mandatory check per case, using exactly one
allowlisted read-only tool call, and returns an advisory InvestigationResult.

Hard rules (enforced by code, verified by injection_tests.py):
  - Never calls backend.checkers itself — checker results are read from the
    CaseView the backend already produced. This module can look things up;
    it cannot grade evidence.
  - Which tool gets called is decided ONLY by which mandatory check is
    unresolved (a fixed, hardcoded mapping below). Nothing in the shipment
    event — least of all `event.notes` or `photo_metadata_ref`, which are
    untrusted free text — has any influence over tool selection.
  - `event.notes` / `photo_metadata_ref` are only ever treated as inert
    strings to display back to a human, truncated and never interpreted,
    executed, or treated as an instruction, a claimed approval, or a reason
    to skip a check.
  - At most one tool call per investigation (CALL_BUDGET = 1). A tool
    failure, a validation error on its arguments, or exceeding the budget
    all fall through to the same place: hand the case to the operator with
    no resolution claimed — but the ATTEMPT itself is still recorded (see
    ToolCallRecord.succeeded) rather than silently dropped, so Raja's
    evaluation and any later audit can see what was tried and why it failed.
  - `suggests_resolved` is true ONLY when the tool both found something AND
    that tool is capable of addressing the SPECIFIC reason the check is
    unresolved (see _RESOLVABLE_DETAILS below). A checker can fail/go
    UNKNOWN for several different reasons under one checker_name, and the
    fixed tool mapping only ever investigates one of them — finding
    something unrelated (a checkpoint that exists for a different handoff
    type reason than why location actually failed; a receiver who'd be a
    valid delegate when the real failure was a HANDLER/custody mismatch)
    must never be reported as resolving a problem it never touched.
  - This module cannot change a CheckerResult, decide policy, approve, or
    execute anything. backend.policy.evaluate_policy() remains the sole
    source of the actual decision; InvestigationResult is a recommendation
    attached for the human reviewer, nothing more.

STATUS (2026-10-12, after Bhone's backend fixes + agent/orchestrator.py):
backend.checkers now fails identity_authority CLOSED as UNKNOWN rather than
FAIL when a receiver isn't pre-listed, and an authenticated POST
/cases/{id}/recheck endpoint now exists (service_auth-gated, a credential
separate from courier/supervisor) that re-runs the real deterministic
checks against the trusted store and bumps the case version.

agent/orchestrator.investigate_and_maybe_recheck() now closes the first
half of the loop: it runs this module's run_investigation(), best-effort
appends the InvestigationResult to the backend's audit chain
(agent/audit_log.py), and — only when `suggests_resolved` is True — calls
the backend's /recheck through agent/recheck_client.py. This module itself
still stays a pure function on purpose (see the hard rules above): it does
not hold an HTTP client or the service credential; the orchestrator is a
separate, auditable layer on top of it, by design.

One thing remains genuinely open, unaffected by any of that wiring:
lookup_delegation/lookup_checkpoint (see tools.py's NOTE ON SCOPE) only
re-read the SAME static manifest row check_identity_authority already read
at evaluation time — there is still no endpoint to write a newly-registered
delegate/checkpoint into the trusted manifest after seed_trusted (which is
fixture-only, not public). So calling /recheck today will faithfully
reproduce the same UNKNOWN; it cannot yet resolve anything a human hasn't
separately updated in the trusted store through some channel that doesn't
exist yet. `suggests_resolved` therefore still never gets treated as
authoritative anywhere — recheck_case() on the backend always re-derives
its own answer from trusted data, never from this module's opinion.
"""
from __future__ import annotations

from backend.models import CaseView, Status

from agent.models import InvestigationResult, ToolCallRecord, ToolName
from agent.tools import ToolRegistry

CALL_BUDGET = 1

# Fixed mapping: which check being unresolved selects which tool.
# This is the ENTIRE tool-selection logic — nothing else participates.
_CHECK_TO_TOOL: dict[str, ToolName] = {
    "parcel_code_integrity": "lookup_custody_history",
    "identity_authority": "lookup_delegation",
    "location": "lookup_checkpoint",
}

# A checker can fail/go UNKNOWN for several structurally different reasons
# under the SAME checker_name (see backend/checkers.py), but the mapped
# tool above only ever investigates ONE of those reasons. A tool's
# `found=True` must never be read as "this specific failure is resolved"
# unless the original unresolved detail is one this tool actually
# addresses — otherwise finding *something* unrelated (a checkpoint that
# exists for a different reason than why location failed; a receiver that
# would be irrelevant to a handler/custody mismatch) gets reported as
# resolving a problem it never touched. This dict is the explicit
# allowlist of (tool, detail) pairs where `found=True` is a meaningful
# resolution signal. It is intentionally coupled to checkers.py's exact
# wording, the same way injection_tests.py's fixtures are — keep both in
# sync if that wording changes.
_RESOLVABLE_DETAILS: dict[ToolName, frozenset[str]] = {
    "lookup_delegation": frozenset({
        "Receiver authorization not found; verify registered receiver/delegation",
    }),
    "lookup_checkpoint": frozenset({
        "No trusted approved checkpoint for this handoff type",
    }),
    # lookup_custody_history can NEVER resolve parcel_code_integrity — its
    # own docstring says so ("never to override a current check result").
    # It still runs (useful context: prior flags, repeat submissions) but
    # its `found` is never treated as a resolution signal for any detail —
    # a shipment having *some* audit history is true almost immediately
    # after the first submission and says nothing about whether a code was
    # replayed, expired, or mismatched.
    "lookup_custody_history": frozenset(),
}

NOTES_PREVIEW_MAX_CHARS = 200


def _safe_notes_preview(notes: str | None) -> str | None:
    """Returns notes as an inert, truncated display string only. Never
    parsed, never evaluated, never used as input to any decision.
    """
    if not notes:
        return None
    flattened = " ".join(notes.split())
    if len(flattened) > NOTES_PREVIEW_MAX_CHARS:
        flattened = flattened[:NOTES_PREVIEW_MAX_CHARS] + "…"
    return flattened


def _build_tool_arguments(tool_name: ToolName, case: CaseView) -> dict:
    event = case.event
    if tool_name == "lookup_delegation":
        return {"shipment_id": event.shipment_id, "candidate_receiver_id": event.receiver_id}
    if tool_name == "lookup_checkpoint":
        return {"shipment_id": event.shipment_id, "handoff_type": event.handoff_type}
    if tool_name == "lookup_custody_history":
        return {"shipment_id": event.shipment_id}
    raise ValueError(f"Unmapped tool: {tool_name}")  # unreachable given _CHECK_TO_TOOL


def run_investigation(case: CaseView, registry: ToolRegistry) -> InvestigationResult:
    unresolved = [c for c in case.checks if c.status != Status.PASS]

    notes_preview = _safe_notes_preview(case.event.notes)
    notes_note = (
        f" (Submitted note on file, shown for context only, not acted on: \"{notes_preview}\")"
        if notes_preview
        else ""
    )

    if not unresolved:
        return InvestigationResult(
            shipment_id=case.shipment_id,
            unresolved_check=None,
            tool_call=None,
            reasoning="All mandatory checks already passed; no investigation needed." + notes_note,
            suggests_resolved=True,
        )

    # Only ever investigate the first unresolved check — CALL_BUDGET = 1
    # means one tool call per pass, matching "the LLM may select ONE
    # allowlisted lookup" in the proposal.
    target = unresolved[0]
    tool_name = _CHECK_TO_TOOL.get(target.checker_name)

    if tool_name is None:
        return InvestigationResult(
            shipment_id=case.shipment_id,
            unresolved_check=target.checker_name,
            tool_call=None,
            reasoning=(
                f"'{target.checker_name}' is unresolved ({target.status.value}: {target.detail}) "
                "but has no mapped investigation tool." + notes_note
            ),
            unresolved_questions=[target.detail],
        )

    arguments = _build_tool_arguments(tool_name, case)

    try:
        result = registry.call(tool_name, arguments)
    except Exception as exc:  # noqa: BLE001 - tool failure/timeout -> operator review, never silent
        # Record the ATTEMPT even though it failed — a failure that drops
        # to tool_call=None is indistinguishable from "no tool call was
        # ever made" (the no-mapped-tool branch above), which hides exactly
        # the attempt/error information Raja's evaluation needs to measure.
        failed_record = ToolCallRecord(
            tool_name=tool_name,
            arguments={k: str(v) for k, v in arguments.items()},
            succeeded=False,
            result_summary=f"{exc.__class__.__name__}: {str(exc)[:200]}",
            evidence_ids=[],
        )
        return InvestigationResult(
            shipment_id=case.shipment_id,
            unresolved_check=target.checker_name,
            tool_call=failed_record,
            reasoning=(
                f"'{target.checker_name}' is unresolved ({target.status.value}: {target.detail}). "
                f"Investigation tool '{tool_name}' failed ({exc.__class__.__name__}); "
                "routing to operator review rather than guessing." + notes_note
            ),
            unresolved_questions=[target.detail],
        )

    call_record = ToolCallRecord(
        tool_name=tool_name,
        arguments={k: str(v) for k, v in arguments.items()},
        result_summary=str(result.get("detail", "")),
        evidence_ids=list(result.get("evidence_ids", [])),
    )

    tool_found = bool(result.get("found"))
    # Gate on whether this tool can even SPEAK to the specific reason the
    # check is unresolved (see _RESOLVABLE_DETAILS above) — not just on
    # whether it found *something*. A checkpoint existing for the right
    # handoff type says nothing about a location check that actually failed
    # for spoofed/mock GPS, a stale timestamp, or a distance mismatch; a
    # receiver being a valid delegate says nothing about an identity check
    # that actually failed because the HANDLER didn't match custody.
    addresses_this_detail = target.detail in _RESOLVABLE_DETAILS.get(tool_name, frozenset())
    resolved = tool_found and addresses_this_detail

    if not tool_found:
        outcome_note = " This does not resolve it; routing to the operator."
    elif addresses_this_detail:
        outcome_note = (
            " This supports resolving the check, but only the backend's own re-evaluation "
            "can change the recorded decision; routing to the operator either way."
        )
    else:
        outcome_note = (
            " The tool found something, but not about the specific reason this check is "
            f"unresolved ({target.detail!r}); NOT treated as resolving it. Routing to the operator."
        )

    reasoning = (
        f"'{target.checker_name}' was unresolved ({target.status.value}: {target.detail}). "
        f"Ran '{tool_name}' — {result.get('detail', 'no detail returned')}."
        + outcome_note
        + notes_note
    )

    return InvestigationResult(
        shipment_id=case.shipment_id,
        unresolved_check=target.checker_name,
        tool_call=call_record,
        reasoning=reasoning,
        unresolved_questions=[] if resolved else [target.detail],
        suggests_resolved=resolved,
    )
