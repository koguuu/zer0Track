"""SQLite persistence and atomic custody updates; never expose seed to public API."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.audit import append_audit, log_approval, setup_audit, verify_audit_chain
from backend.checkers import run_mandatory_checks
from backend.models import (
    AgentDecision, ApprovalRecord, ApprovalRequest, CaseView, CheckerResult,
    RecheckRequest, ShipmentEvent, TransferCodeRecord, TrustedShipmentRecord,
)
from backend.policy import evaluate_policy


class BackendError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


def _json(model) -> str:
    return model.model_dump_json()


def _utc_iso(dt: datetime) -> str:
    """Canonicalize timestamp serialization for new database writes."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("Stored timestamps must include a timezone offset")
    return dt.astimezone(timezone.utc).isoformat()


def _fingerprint(event: ShipmentEvent) -> str:
    return hashlib.sha256(_json(event).encode()).hexdigest()


class Store:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.setup()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA foreign_keys=ON")
            with conn:
                yield conn
        finally:
            conn.close()

    def setup(self) -> None:
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""CREATE TABLE IF NOT EXISTS manifests(
                shipment_id TEXT PRIMARY KEY, manifest TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS transfer_codes(
                code_ref TEXT PRIMARY KEY, shipment_id TEXT NOT NULL,
                transaction_id TEXT NOT NULL, receiver_id TEXT NOT NULL,
                issued_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                consumed_at TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS cases(
                shipment_id TEXT PRIMARY KEY, version INTEGER NOT NULL,
                state TEXT NOT NULL, event TEXT NOT NULL, event_fingerprint TEXT NOT NULL,
                checks TEXT NOT NULL, decision TEXT NOT NULL, approval TEXT)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS seen_events(
                event_id TEXT PRIMARY KEY, shipment_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS seen_approvals(
                request_id TEXT PRIMARY KEY, shipment_id TEXT NOT NULL,
                payload_fingerprint TEXT NOT NULL, response TEXT NOT NULL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS handler_evaluations(
                shipment_id TEXT PRIMARY KEY, authenticated_handler_id TEXT NOT NULL)""")
            conn.execute('CREATE TABLE IF NOT EXISTS staff_login_attempts (operator_id TEXT PRIMARY KEY, failed_attempts INTEGER NOT NULL DEFAULT 0, locked_until TEXT, updated_at TEXT NOT NULL)')
            conn.execute(
                "CREATE TABLE IF NOT EXISTS staff_sessions ("
                "token_hash TEXT PRIMARY KEY, "
                "operator_id TEXT NOT NULL, "
                "expires_at TEXT NOT NULL)"
            )
            conn.execute("""CREATE TABLE IF NOT EXISTS courier_rate_events(
                courier_id TEXT NOT NULL, recorded_at TEXT NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_courier_rate ON courier_rate_events(courier_id, recorded_at)")
            conn.execute("""CREATE TABLE IF NOT EXISTS pending_dual_approvals(
                shipment_id TEXT NOT NULL, case_version INTEGER NOT NULL,
                action TEXT NOT NULL, operator_id TEXT NOT NULL,
                request_id TEXT NOT NULL UNIQUE, reason TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(shipment_id, case_version, action, operator_id))""")
            setup_audit(conn)

    def seed_trusted(self, manifest: TrustedShipmentRecord, code: TransferCodeRecord) -> None:
        """Local offline fixture operation only. Not a public HTTP endpoint."""
        if manifest.shipment_id != code.shipment_id:
            raise ValueError("Code and manifest must belong to the same shipment")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR REPLACE INTO manifests VALUES (?,?)",
                         (manifest.shipment_id, _json(manifest)))
            conn.execute("""INSERT OR REPLACE INTO transfer_codes
                (code_ref, shipment_id, transaction_id, receiver_id, issued_at, expires_at, consumed_at)
                VALUES (?,?,?,?,?,?,?)""", (
                code.code_ref, code.shipment_id, code.transaction_id, code.receiver_id,
                _utc_iso(code.issued_at), _utc_iso(code.expires_at),
                _utc_iso(code.consumed_at) if code.consumed_at else None))
            conn.commit()

    @staticmethod
    def _trusted(conn: sqlite3.Connection, shipment_id: str) -> TrustedShipmentRecord | None:
        row = conn.execute("SELECT manifest FROM manifests WHERE shipment_id=?", (shipment_id,)).fetchone()
        return TrustedShipmentRecord.model_validate_json(row["manifest"]) if row else None

    @staticmethod
    def _code(conn: sqlite3.Connection, ref: str) -> TransferCodeRecord | None:
        row = conn.execute("SELECT * FROM transfer_codes WHERE code_ref=?", (ref,)).fetchone()
        return TransferCodeRecord.model_validate(dict(row)) if row else None

    @staticmethod
    def _from_row(row: sqlite3.Row) -> CaseView:
        return CaseView(shipment_id=row["shipment_id"], version=row["version"], state=row["state"],
            event=ShipmentEvent.model_validate_json(row["event"]),
            checks=[CheckerResult.model_validate(x) for x in json.loads(row["checks"])],
            decision=AgentDecision.model_validate_json(row["decision"]),
            approval=ApprovalRecord.model_validate_json(row["approval"]) if row["approval"] else None)

    def get_case(self, shipment_id: str) -> CaseView:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM cases WHERE shipment_id=?", (shipment_id,)).fetchone()
            if row is None:
                raise BackendError(404, "Shipment case not found")
            return self._from_row(row)

    def list_cases(self, *, state: str | None = None, limit: int = 100) -> list[CaseView]:
        """Read-only listing for dashboards/operators. Newest first (by rowid,
        since cases are upserted in place and don't carry a created_at column).
        `state` filters to an exact CaseView.state value (e.g. "PENDING");
        omit it to list all states. `limit` is capped at 500 to keep a
        dashboard call cheap -- callers needing more should page by state.
        """
        limit = max(1, min(limit, 500))
        with self.connect() as conn:
            if state is not None:
                rows = conn.execute(
                    "SELECT * FROM cases WHERE state=? ORDER BY rowid DESC LIMIT ?",
                    (state, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM cases ORDER BY rowid DESC LIMIT ?", (limit,)
                ).fetchall()
            return [self._from_row(row) for row in rows]

    def enforce_courier_rate_limit(self, courier_id: str, limit: int = 10, window_seconds: int = 60) -> None:
        """Persistent rolling window shared by all API workers using this SQLite DB."""
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(seconds=window_seconds)).isoformat()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM courier_rate_events WHERE recorded_at <= ?", (cutoff,))
            count = conn.execute("SELECT COUNT(*) FROM courier_rate_events WHERE courier_id=?",
                                 (courier_id,)).fetchone()[0]
            if count >= limit:
                raise BackendError(429, "Courier evaluation rate limit exceeded; retry later")
            conn.execute("INSERT INTO courier_rate_events VALUES (?,?)", (courier_id, now.isoformat()))

    def evaluate_case(self, event: ShipmentEvent, authenticated_handler_id: str) -> CaseView:
        fp = _fingerprint(event)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous_event = conn.execute("SELECT * FROM seen_events WHERE event_id=?", (event.event_id,)).fetchone()
            if previous_event and (previous_event["shipment_id"] != event.shipment_id or previous_event["fingerprint"] != fp):
                raise BackendError(409, "Event ID reused with different evidence")
            existing = conn.execute("SELECT * FROM cases WHERE shipment_id=?", (event.shipment_id,)).fetchone()
            if previous_event and existing and existing["event_fingerprint"] != fp:
                raise BackendError(409, "Old event ID cannot replace newer evidence")
            if existing and existing["state"] in {"ACCEPTED", "HELD", "REJECTED", "ESCALATED"}:
                raise BackendError(409, "Case already finalized or held; separate release workflow required")
            if existing and existing["event_fingerprint"] == fp:
                previous_handler = conn.execute(
                    "SELECT authenticated_handler_id FROM handler_evaluations WHERE shipment_id=?",
                    (event.shipment_id,)).fetchone()
                if not previous_handler or previous_handler["authenticated_handler_id"] != authenticated_handler_id:
                    raise BackendError(403, "Authenticated handler differs from original submission")
                # Retries are idempotent, even when the original event ID is unchanged.
                return self._from_row(existing)
            trusted = self._trusted(conn, event.shipment_id)
            code = self._code(conn, event.transfer_code_ref)
            checks = run_mandatory_checks(event, trusted, code, authenticated_handler_id)
            decision = evaluate_policy(checks)
            version = existing["version"] + 1 if existing else 1
            conn.execute("""INSERT INTO cases(shipment_id,version,state,event,event_fingerprint,checks,decision,approval)
                VALUES(?,?,?,?,?,?,?,NULL)
                ON CONFLICT(shipment_id) DO UPDATE SET version=excluded.version,
                state=excluded.state, event=excluded.event, event_fingerprint=excluded.event_fingerprint,
                checks=excluded.checks, decision=excluded.decision,approval=NULL""", (
                event.shipment_id, version, "PENDING", _json(event), fp,
                json.dumps([x.model_dump(mode="json") for x in checks]), _json(decision)))
            conn.execute("""INSERT INTO handler_evaluations(shipment_id,authenticated_handler_id)
                VALUES(?,?) ON CONFLICT(shipment_id) DO UPDATE SET
                authenticated_handler_id=excluded.authenticated_handler_id""", (
                event.shipment_id, authenticated_handler_id))
            if not previous_event:
                conn.execute("INSERT INTO seen_events VALUES(?,?,?)", (event.event_id, event.shipment_id, fp))
            append_audit(conn, event.shipment_id, "EVALUATION",
                         {"event_id": event.event_id, "case_version": version,
                          "check_results": [x.model_dump(mode="json") for x in checks],
                          "policy_decision": decision.model_dump(mode="json")})
            conn.commit()
            return self.get_case(event.shipment_id)

    def recheck_case(self, shipment_id: str, req: RecheckRequest) -> CaseView:
        """Refresh mandatory checks using trusted records held by the backend.

        Does not accept an LLM's `suggests_resolved` as authoritative. A trusted
        authorization/checkpoint update must happen through an independent channel
        before this call can actually change a check from UNKNOWN to PASS.
        """
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM cases WHERE shipment_id=?", (shipment_id,)).fetchone()
            if row is None:
                raise BackendError(404, "Case not found")
            case = self._from_row(row)
            if case.state != "PENDING":
                raise BackendError(409, "Only pending cases can be rechecked")
            if req.expected_case_version != case.version:
                raise BackendError(409, "Stale recheck request: evidence version changed")
            handler = conn.execute("SELECT authenticated_handler_id FROM handler_evaluations WHERE shipment_id=?",
                                   (shipment_id,)).fetchone()
            trusted = self._trusted(conn, shipment_id)
            code = self._code(conn, case.event.transfer_code_ref)
            checks = run_mandatory_checks(case.event, trusted, code,
                                          handler["authenticated_handler_id"] if handler else None)
            decision = evaluate_policy(checks)
            # A recheck always establishes a NEW evidence version, invalidating
            # any approval relying on the old version, even if statuses are unchanged.
            version = case.version + 1
            conn.execute("""UPDATE cases SET version=?,checks=?,decision=?,approval=NULL
                WHERE shipment_id=? AND state='PENDING'""", (
                version, json.dumps([x.model_dump(mode="json") for x in checks]),
                _json(decision), shipment_id))
            conn.execute("DELETE FROM pending_dual_approvals WHERE shipment_id=?", (shipment_id,))
            append_audit(conn, shipment_id, "RECHECK",
                         {"event_id": case.event.event_id, "case_version": version,
                          "reason": req.reason,
                          "check_results": [x.model_dump(mode="json") for x in checks],
                          "policy_decision": decision.model_dump(mode="json")})
            conn.commit()
            return CaseView(shipment_id=shipment_id, version=version, state="PENDING",
                            event=case.event, checks=checks, decision=decision)

    def pending_approvals(self, shipment_id: str) -> list[dict]:
        """Operator-facing summary of unfinalized two-person decisions."""
        case = self.get_case(shipment_id)
        with self.connect() as conn:
            rows = conn.execute("""SELECT action,operator_id,created_at FROM pending_dual_approvals
                WHERE shipment_id=? AND case_version=? ORDER BY created_at""",
                (shipment_id, case.version)).fetchall()
        return [dict(row) for row in rows] if case.state == "PENDING" else []

    def approve_case(self, shipment_id: str, req: ApprovalRequest, operator_id: str) -> CaseView:
        approval_fp = hashlib.sha256(f"{shipment_id}:{operator_id}:{req.model_dump_json()}".encode()).hexdigest()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            retry = conn.execute("SELECT * FROM seen_approvals WHERE request_id=?", (req.approval_request_id,)).fetchone()
            if retry:
                if retry["shipment_id"] != shipment_id or retry["payload_fingerprint"] != approval_fp:
                    raise BackendError(409, "Approval request ID reused with different content")
                return CaseView.model_validate_json(retry["response"])
            row = conn.execute("SELECT * FROM cases WHERE shipment_id=?", (shipment_id,)).fetchone()
            if not row:
                raise BackendError(404, "Case not found")
            case = self._from_row(row)
            if case.state != "PENDING":
                raise BackendError(409, "Case is not pending")
            if req.expected_case_version != case.version:
                raise BackendError(409, "Stale approval: case evidence version changed")
            trusted = self._trusted(conn, shipment_id)
            code = self._code(conn, case.event.transfer_code_ref)
            # Important: the authenticated handler is derived from the recorded trusted
            # authorization at evaluation, but live evaluation must use the actual
            # handler authenticated at submission. See the stored handler identity below.
            original = conn.execute("SELECT authenticated_handler_id FROM handler_evaluations WHERE shipment_id=?", (shipment_id,)).fetchone()
            handler_id = original["authenticated_handler_id"] if original else None
            now = datetime.now(timezone.utc)
            checks = run_mandatory_checks(case.event, trusted, code, handler_id, now=now)
            decision = evaluate_policy(checks)
            # Changes to independently trusted evidence require a new case
            # version before any approval can execute. Agent conclusions alone
            # never refresh a case; use the authenticated /recheck endpoint.
            if checks != case.checks or decision != case.decision:
                raise BackendError(409, "Evidence or policy has changed; recheck case before approval")
            if req.action in {"reject", "escalate"}:
                # Both approvals must refer to this exact shipment, evidence version
                # and action. A single identity cannot approve twice.
                prior = conn.execute("""SELECT * FROM pending_dual_approvals
                    WHERE shipment_id=? AND case_version=? AND action=?""",
                    (shipment_id, case.version, req.action)).fetchall()
                if any(item["operator_id"] == operator_id for item in prior):
                    raise BackendError(409, "A different supervisor must provide the second approval")
                conn.execute("""INSERT INTO pending_dual_approvals
                    (shipment_id,case_version,action,operator_id,request_id,reason,created_at)
                    VALUES (?,?,?,?,?,?,?)""", (shipment_id, case.version, req.action,
                    operator_id, req.approval_request_id, req.reason, _utc_iso(now)))
                if not prior:
                    append_audit(conn, shipment_id, "DUAL_APPROVAL_REQUESTED",
                                 {"case_version": case.version, "action": req.action,
                                  "operator_id": operator_id, "request_id": req.approval_request_id})
                    # Keep case PENDING until an independent supervisor confirms.
                    conn.commit()
                    return case
                # The first approval is only a proposal; the second supervisor
                # authorizes the final transition after fresh evidence checks.
            if req.action == "accept":
                if decision.decision != "ACCEPT_READY":
                    raise BackendError(409, "Mandatory evidence is not all PASS; cannot accept")
                # The mandatory checker already compared parsed aware datetimes
                # against UTC time *inside* this BEGIN IMMEDIATE transaction.
                # Do NOT compare mixed-offset ISO timestamps as SQLite TEXT:
                # lexical order is not chronological order.
                # The consumed_at IS NULL CAS still guarantees single-use.
                cur = conn.execute("""UPDATE transfer_codes SET consumed_at=?
                    WHERE code_ref=? AND shipment_id=? AND transaction_id=? AND receiver_id=?
                    AND consumed_at IS NULL""", (
                    _utc_iso(now), case.event.transfer_code_ref, shipment_id,
                    case.event.transaction_id, case.event.receiver_id))
                if cur.rowcount != 1:
                    raise BackendError(409, "Code consumed or invalid during acceptance")
                assert trusted is not None
                trusted.custody_state = "ACCEPTED"
                conn.execute("UPDATE manifests SET manifest=? WHERE shipment_id=?", (_json(trusted), shipment_id))
                new_state = "ACCEPTED"
            else:
                if req.action == "hold":
                    new_state = "HELD"
                elif req.action == "escalate":
                    new_state = "ESCALATED"
                else:
                    new_state = "REJECTED"
            record = ApprovalRecord(approval_id=str(uuid.uuid4()), shipment_id=shipment_id,
                evidence_version=case.version, operator_id=operator_id,
                action=req.action, reason=req.reason)
            updated = CaseView(shipment_id=shipment_id, version=case.version, state=new_state,
                event=case.event, checks=checks, decision=decision, approval=record)
            conn.execute("""UPDATE cases SET state=?, checks=?, decision=?, approval=?
                WHERE shipment_id=? AND version=?""", (
                new_state, json.dumps([x.model_dump(mode="json") for x in checks]),
                _json(decision), _json(record), shipment_id, case.version))
            log_approval(record, conn)
            conn.execute("DELETE FROM pending_dual_approvals WHERE shipment_id=?", (shipment_id,))
            conn.execute("INSERT INTO seen_approvals VALUES(?,?,?,?)", (
                req.approval_request_id, shipment_id, approval_fp, _json(updated)))
            conn.commit()
            return updated
