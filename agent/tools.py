"""The agent's fixed, read-only tool allowlist.

Security rules enforced here, not left to the caller:
  1. Every argument is validated against a strict Pydantic schema before any
     query runs — no raw dict ever reaches SQL or the trusted-record lookup.
  2. Every SQL query is fully parameterized (`?` placeholders). No f-string
     or .format() ever builds a query from caller-supplied data.
  3. These tools only ever SELECT. None can write, update, or delete.
  4. ToolRegistry is the only way to invoke a tool by name — there is no
     dynamic dispatch (no getattr(module, name), no eval). Adding a tool
     means adding it to ALLOWED_TOOLS explicitly, in code review.

NOTE ON SCOPE: as of the current backend (see TODO in investigator.py),
lookup_delegation / lookup_checkpoint can only re-read data already present
in the trusted manifest written at seed time — there is no separate
delegation-registration source yet. That is a real limitation, not simulated
here: if a delegate genuinely was never registered, the lookup correctly
finds nothing. Once backend/storage.py exposes a live delegation/checkpoint
registry, only the query body inside each tool needs to change — the
allowlist contract (name, arguments, return shape) stays the same.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.storage import Store


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class LookupDelegationArgs(StrictModel):
    shipment_id: str = Field(min_length=1, max_length=128)
    candidate_receiver_id: str = Field(min_length=1, max_length=128)


class LookupCheckpointArgs(StrictModel):
    shipment_id: str = Field(min_length=1, max_length=128)
    handoff_type: Literal["doorstep", "guardhouse", "depot", "warehouse"]


class LookupCustodyHistoryArgs(StrictModel):
    shipment_id: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=20, ge=1, le=100)


def lookup_delegation(store: Store, args: LookupDelegationArgs) -> dict:
    """Read-only: is candidate_receiver_id registered as an authorized
    delegate (or primary receiver) on this shipment's trusted manifest?
    """
    with store.connect() as conn:
        row = conn.execute(
            "SELECT manifest FROM manifests WHERE shipment_id = ?",
            (args.shipment_id,),
        ).fetchone()
    if row is None:
        return {"found": False, "detail": "No trusted manifest for this shipment", "evidence_ids": []}
    manifest = json.loads(row["manifest"])
    delegates = set(manifest.get("authorized_delegate_ids", []))
    receivers = set(manifest.get("authorized_receiver_ids", []))
    is_authorized = args.candidate_receiver_id in (delegates | receivers)
    return {
        "found": is_authorized,
        "detail": (
            f"{args.candidate_receiver_id} is a registered "
            f"{'delegate' if args.candidate_receiver_id in delegates else 'receiver'}"
            if is_authorized
            else f"{args.candidate_receiver_id} is not in the trusted delegate/receiver list"
        ),
        "evidence_ids": [f"manifest:{args.shipment_id}"],
    }


def lookup_checkpoint(store: Store, args: LookupCheckpointArgs) -> dict:
    """Read-only: list the trusted approved checkpoints for this shipment
    that match the claimed handoff type.
    """
    with store.connect() as conn:
        row = conn.execute(
            "SELECT manifest FROM manifests WHERE shipment_id = ?",
            (args.shipment_id,),
        ).fetchone()
    if row is None:
        return {"found": False, "detail": "No trusted manifest for this shipment", "evidence_ids": []}
    manifest = json.loads(row["manifest"])
    matches = [c for c in manifest.get("checkpoints", []) if c.get("handoff_type") == args.handoff_type]
    return {
        "found": bool(matches),
        "detail": (
            f"{len(matches)} approved checkpoint(s) registered for handoff type '{args.handoff_type}'"
            if matches
            else f"No approved checkpoint registered for handoff type '{args.handoff_type}'"
        ),
        "checkpoint_ids": [c.get("checkpoint_id") for c in matches],
        "evidence_ids": [f"manifest:{args.shipment_id}"],
    }


def lookup_custody_history(store: Store, args: LookupCustodyHistoryArgs) -> dict:
    """Read-only: recent audit-log entries for this shipment (evaluations,
    approvals) — used to check for prior flags or repeated submissions,
    never to override a current check result.
    """
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT seq, kind, recorded_at FROM audit_log "
            "WHERE shipment_id = ? ORDER BY seq DESC LIMIT ?",
            (args.shipment_id, args.limit),
        ).fetchall()
    entries = [{"seq": r["seq"], "kind": r["kind"], "recorded_at": r["recorded_at"]} for r in rows]
    return {
        "found": bool(entries),
        "detail": f"{len(entries)} prior audit entries for this shipment",
        "entries": entries,
        "evidence_ids": [f"audit:{args.shipment_id}:{e['seq']}" for e in entries],
    }


# The allowlist. Keys MUST exactly match agent.models.ToolName.
ALLOWED_TOOLS = {
    "lookup_delegation": (LookupDelegationArgs, lookup_delegation),
    "lookup_checkpoint": (LookupCheckpointArgs, lookup_checkpoint),
    "lookup_custody_history": (LookupCustodyHistoryArgs, lookup_custody_history),
}


class ToolRegistry:
    """The only entry point for invoking a tool. Enforces the allowlist and
    argument validation; nothing else in this codebase should call the
    lookup_* functions directly.
    """

    def __init__(self, store: Store):
        self._store = store

    def available_tools(self) -> list[str]:
        return list(ALLOWED_TOOLS.keys())

    def call(self, tool_name: str, raw_arguments: dict) -> dict:
        if tool_name not in ALLOWED_TOOLS:
            raise ValueError(f"Tool '{tool_name}' is not on the allowlist")
        args_model, fn = ALLOWED_TOOLS[tool_name]
        # Validates and coerces — raises pydantic.ValidationError on anything
        # malformed or unexpected, before the function body ever runs.
        args = args_model.model_validate(raw_arguments)
        try:
            return fn(self._store, args)
        except sqlite3.Error as exc:
            # Never leak raw SQL errors (could reveal schema) to the caller.
            raise RuntimeError("Lookup failed") from exc
