"""Appends the agent's own investigation results to the backend's
tamper-evident, hash-linked audit log — as a plain log entry, never as
anything that changes a CheckerResult, case version, or decision.

Why this exists: before this module, InvestigationResult lived only in
memory for the duration of one call. If an operator (or a later audit)
asked "what did the agent actually look at, and what did it conclude?"
after the fact, there was no record anywhere. Writing it here closes that
gap using the same audit_log table and hash chain backend.storage.py
already writes EVALUATION/RECHECK/APPROVAL entries into, so a full
timeline for a shipment — human and agent actions together — comes back
from the one existing GET /cases/{id}/audit endpoint.

This module does NOT decide anything and must never be allowed to block
the actual investigation or decision flow: callers treat a failure here as
best-effort (see agent/orchestrator.py), the same way a failed tool call
falls through to operator review rather than crashing.
"""
from __future__ import annotations

from backend.audit import append_audit
from backend.storage import Store

from agent.models import InvestigationResult

AUDIT_KIND = "AGENT_INVESTIGATION"


def log_investigation(store: Store, result: InvestigationResult) -> int:
    """Appends `result` to its shipment's audit chain and returns the new
    entry's sequence number. Raises whatever the underlying Store/sqlite3
    call raises — callers that want best-effort behavior must catch around
    this themselves; this function does not silently swallow errors, so a
    caller can't mistake a failed write for a successful one.
    """
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        seq = append_audit(conn, result.shipment_id, AUDIT_KIND, result.model_dump(mode="json"))
        conn.commit()
        return seq
