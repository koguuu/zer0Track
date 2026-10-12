"""Kogu's bridge into Raja's comparison harness for the "rules_and_llm" arm.

Mirrors evaluate_with_bhone's shape and restrictions exactly (pure-ish,
evaluation_only=True, no Store/approve/evaluate against a live backend) and
adds exactly one thing on top: running agent.investigator.run_investigation
against an EPHEMERAL, per-call, throwaway SQLite Store seeded only from
this fixture's own fixture_context (trusted_shipment + transfer_code) --
never a shared or persistent database, never the live backend's actual
audit log, never a network call. That's deliberate: this harness measures
the investigator's own logic, not agent/orchestrator.py's /recheck wiring
(which needs a live backend, service credentials, and a real persistent
audit chain -- none of which belong in an evaluation_only comparator).

Honesty rules this file follows, because Raja's harness checks for exactly
these dishonesties:
  - llm_used reflects whether a real LLM call actually ran for THIS row,
    nothing more. Tool SELECTION is still always a fixed, hardcoded mapping
    (see agent/investigator.py's own docstring) -- no LLM ever picks a tool
    or influences a decision. The one real LLM call in this codebase is
    agent.injection_detector's advisory notes classifier (see its module
    docstring for the isolation guarantee); llm_used is True only when that
    classifier actually ran and returned a signal for this row, which
    requires both an LLM_client configured (ZTRACK_LLM_API_KEY /
    ANTHROPIC_API_KEY set) and event.notes present. With no key configured
    -- the default in this sandbox and in CI -- it stays False, same as
    before, and every existing test keeps passing unchanged.
  - agent_invoked is True only when run_investigation actually ran (i.e.
    was not skipped by a policy gate below) -- not merely because this
    adapter was called.
  - notes_exposed is computed from whether event.notes was actually passed
    through to the investigator and could appear in its reasoning, not
    hardcoded. See _apply_policy_gates.
  - checks/action/requires_human_approval always come from the BACKEND's
    own run_mandatory_checks()/evaluate_policy() output, never from the
    investigation. agent.investigator.run_investigation cannot change a
    CheckerResult or a decision (see its own hard rules) and this adapter
    doesn't either -- suggests_resolved is reported only inside the
    explanation text, as context for a human, exactly like the live system.
"""
from __future__ import annotations

import copy
import os
import tempfile
from datetime import datetime
from pathlib import Path

from evaluation.run_eval import PipelineUnavailable, evaluate_with_bhone

_NAMES = {"parcel_code_integrity": "parcel_code", "identity_authority": "identity", "location": "location"}
_ACTIONS = {"ACCEPT_READY": "ACCEPT", "REQUEST_VERIFICATION": "VERIFY",
            "RECOMMEND_HOLD": "HOLD", "ESCALATE": "ESCALATE"}


def _unresolved_target(checks):
    from backend.models import Status
    for check in checks:
        if check.status != Status.PASS:
            return check
    return None


def _skip_response(*, decision, checks, policy_config, reason):
    return {
        "action": _ACTIONS[decision.decision],
        "checks": {_NAMES[c.checker_name]: c.status.value for c in checks},
        "tool_calls": [], "executed": False, "requires_human_approval": decision.requires_human_approval,
        "llm_used": False, "agent_invoked": False, "notes_exposed": False,
        "explanation": decision.reason + " " + reason,
        "applied_policy_config": copy.deepcopy(policy_config), "investigation_settings_active": True,
        "backend_decision": decision.model_dump(mode="json"),
        "backend_checks": [c.model_dump(mode="json") for c in checks],
        "implementation": "kogu_investigator_skipped_by_policy",
    }


def evaluate_with_kogu(*, fixture, mode, policy_config, evaluation_only):
    """The rules_and_llm half of the team's combined pipeline (see
    `pipeline()` below for the dispatcher Raja's --pipeline flag should
    actually point at).
    """
    if evaluation_only is not True:
        raise ValueError("This bridge is restricted to evaluation_only=True.")
    if mode != "rules_and_llm":
        raise ValueError("Kogu's bridge supports only the rules_and_llm arm; "
                          "route rules_only to evaluate_with_bhone.")
    if fixture.get("fixture_format") != "raja-draft-v2" or fixture.get("source") != "synthetic":
        raise ValueError("Regenerate Raja's public v2 synthetic fixtures before connecting this bridge.")

    from backend.checkers import run_mandatory_checks
    from backend.models import CourierHistory, ShipmentEvent, Status, TransferCodeRecord, TrustedShipmentRecord
    from backend.policy import evaluate_policy
    from backend.storage import Store

    from agent.investigator import run_investigation
    from agent.llm_client import AnthropicInjectionClassifier
    from agent.tools import ToolRegistry

    # Optional and advisory-only (see agent/injection_detector.py) -- None
    # whenever no key is configured, which is the default here. Never
    # affects checks/action/tool selection; only whether injection_signal
    # gets populated below and, honestly, whether llm_used is True.
    llm_client = (
        AnthropicInjectionClassifier()
        if (os.environ.get("ZTRACK_LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
        else None
    )

    event = ShipmentEvent.model_validate(fixture["shipment_event"])
    CourierHistory.model_validate(fixture["courier_history"])  # validated, not consumed by any mandatory check
    context = fixture["fixture_context"]
    trusted = TrustedShipmentRecord.model_validate(context["trusted_shipment"])
    code = TransferCodeRecord.model_validate(context["transfer_code"]) if context["transfer_code"] is not None else None
    now = datetime.fromisoformat(context["reference_time"].replace("Z", "+00:00"))
    if now.tzinfo is None:
        raise ValueError("Synthetic reference clock must include a timezone.")
    handler = context["authenticated_handler_id"]
    if not isinstance(handler, str) or not handler:
        raise ValueError("Provide an independently seeded synthetic authenticated handler.")

    # Backend truth, computed exactly as evaluate_with_bhone does it -- the
    # investigation never participates in this and can never change it.
    checks = run_mandatory_checks(event, trusted, code, handler, now=now)
    decision = evaluate_policy(checks)

    target = _unresolved_target(checks)
    if target is None:
        return _skip_response(decision=decision, checks=checks, policy_config=policy_config,
                               reason="(No unresolved check; nothing to investigate.)")

    # Policy gates, applied BEFORE touching the investigator at all, so a
    # skip is visibly a skip (agent_invoked=False) rather than the
    # investigator quietly doing nothing.
    if target.status == Status.UNKNOWN and not policy_config["investigate_unknown"]:
        return _skip_response(decision=decision, checks=checks, policy_config=policy_config,
                               reason=f"(Policy '{policy_config['name']}' disables investigating UNKNOWN checks.)")
    if target.checker_name == "location" and not policy_config["investigate_location"]:
        return _skip_response(decision=decision, checks=checks, policy_config=policy_config,
                               reason=f"(Policy '{policy_config['name']}' disables investigating location.)")

    investigate_context = bool(policy_config["investigate_context"])
    # When context investigation is off, strip notes/photo_metadata_ref
    # before the investigator ever sees them -- not after. This is the same
    # guarantee the live system relies on (_safe_notes_preview only ever
    # echoes what it's given), applied one level earlier so notes_exposed
    # below is simply "was anything there to expose", not a separate claim
    # to keep in sync.
    event_for_investigation = (
        event if investigate_context else event.model_copy(update={"notes": None, "photo_metadata_ref": None})
    )

    with tempfile.TemporaryDirectory() as tmp:
        # Ephemeral, per-call, throwaway -- never the live backend's DB,
        # never shared across rows, discarded the moment this call returns.
        store = Store(str(Path(tmp) / "eval.sqlite"))
        # seed_trusted's signature requires a code record; when this fixture
        # has none, use an inert placeholder -- none of the three tools
        # (lookup_delegation/lookup_checkpoint/lookup_custody_history) ever
        # read the transfer_codes table, so this cannot affect any result.
        store.seed_trusted(trusted, code if code is not None else _placeholder_code(trusted))

        from backend.models import CaseView
        case = CaseView(
            shipment_id=event.shipment_id, version=1, state="PENDING",
            event=event_for_investigation, checks=checks, decision=decision,
        )
        investigation = run_investigation(case, ToolRegistry(store), llm_client=llm_client)

    tool_calls = []
    if investigation.tool_call is not None:
        tc = investigation.tool_call
        tool_calls.append({
            "name": tc.tool_name,
            "arguments": dict(tc.arguments),
            "status": "OK" if tc.succeeded else "ERROR",
        })

    notes_exposed = investigate_context and bool(event.notes)

    explanation = decision.reason + " | Investigation: " + investigation.reasoning

    return {
        "action": _ACTIONS[decision.decision],
        "checks": {_NAMES[c.checker_name]: c.status.value for c in checks},
        "tool_calls": tool_calls,
        "executed": False,
        "requires_human_approval": decision.requires_human_approval,
        # True only when the injection-signal classifier actually ran and
        # returned a result for this row -- see module docstring. Note: the
        # classifier's verdict never appears in "action"/"checks" above,
        # which come from decision/checks computed before it ever runs.
        "llm_used": investigation.injection_signal is not None,
        "agent_invoked": True,
        "notes_exposed": notes_exposed,
        "explanation": explanation,
        "applied_policy_config": copy.deepcopy(policy_config),
        "investigation_settings_active": True,
        "backend_decision": decision.model_dump(mode="json"),
        "backend_checks": [c.model_dump(mode="json") for c in checks],
        "implementation": "kogu_investigator_fixed_tool_mapping",
        # Extra, non-contractual fields kept for anyone reading results.json
        # by hand -- _validate_response ignores unknown keys.
        "suggests_resolved": investigation.suggests_resolved,
        "unresolved_check": investigation.unresolved_check,
        "injection_signal": (
            investigation.injection_signal.model_dump(mode="json")
            if investigation.injection_signal is not None
            else None
        ),
    }


def _placeholder_code(trusted):
    from datetime import timedelta, timezone
    from backend.models import TransferCodeRecord
    now = datetime.now(timezone.utc)
    return TransferCodeRecord(
        code_ref="EVAL-PLACEHOLDER", shipment_id=trusted.shipment_id, transaction_id="EVAL-PLACEHOLDER",
        receiver_id="EVAL-PLACEHOLDER", issued_at=now - timedelta(days=1), expires_at=now - timedelta(hours=1),
    )


def pipeline(*, fixture, mode, policy_config, evaluation_only):
    """The actual --pipeline target: evaluation.kogu_adapter:pipeline.
    Routes rules_only to Bhone's existing bridge and rules_and_llm to the
    one above, so one adapter covers both non-scan arms.
    """
    if mode == "rules_only":
        return evaluate_with_bhone(fixture=fixture, mode=mode, policy_config=policy_config,
                                    evaluation_only=evaluation_only)
    if mode == "rules_and_llm":
        return evaluate_with_kogu(fixture=fixture, mode=mode, policy_config=policy_config,
                                   evaluation_only=evaluation_only)
    raise PipelineUnavailable(f"No bridge for mode '{mode}'.")
