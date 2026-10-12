"""Tests for the wiring added after the original agent module: audit
persistence of InvestigationResult (agent/audit_log.py) and the
investigation -> /recheck loop (agent/recheck_client.py,
agent/orchestrator.py).

Run with: pytest agent/orchestrator_tests.py -v

Needs ZTRACK_AUDIT_HMAC_KEY set (>=32 chars) in the environment, same as
any other code path that writes to the audit log (backend.audit._secret()
requires it unconditionally) — see conftest-style fixture below.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from backend.audit import get_audit_trail
from backend.models import (
    AgentDecision,
    ApprovedCheckpoint,
    CaseView,
    CheckerResult,
    LocationEvidence,
    ShipmentEvent,
    Status,
    TransferCodeRecord,
    TrustedShipmentRecord,
)
from backend.storage import Store

from agent.audit_log import AUDIT_KIND, log_investigation
from agent.models import InvestigationResult, ToolCallRecord
from agent.orchestrator import investigate_and_maybe_recheck
from agent.recheck_client import RecheckUnavailable, request_recheck


@pytest.fixture(autouse=True)
def audit_key(monkeypatch):
    # Every audit_log write needs this; set it for every test in this file
    # rather than relying on the environment already having it.
    monkeypatch.setenv("ZTRACK_AUDIT_HMAC_KEY", "test-only-hmac-key-at-least-32-characters-xx")


@pytest.fixture(autouse=True)
def no_backend_configured(monkeypatch):
    # Default: no recheck endpoint configured, so tests that don't set up
    # a fake server explicitly get a deterministic RecheckUnavailable.
    monkeypatch.delenv("ZTRACK_API_BASE_URL", raising=False)
    monkeypatch.delenv("ZTRACK_SERVICE_KEY", raising=False)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(str(tmp_path / "test.sqlite"))
    now = datetime.now(timezone.utc)
    manifest = TrustedShipmentRecord(
        shipment_id="GDX1", parcel_id="P1", assigned_handler_id="CR-1", current_custodian_id="CR-1",
        authorized_receiver_ids=["R1"], authorized_delegate_ids=[],
        checkpoints=[ApprovedCheckpoint(checkpoint_id="CK1", handoff_type="doorstep",
                                         latitude=3.118, longitude=101.640, radius_m=50)],
    )
    code = TransferCodeRecord(code_ref="CODE1", shipment_id="GDX1", transaction_id="TX1", receiver_id="R1",
                               issued_at=now - timedelta(minutes=5), expires_at=now + timedelta(minutes=5))
    s.seed_trusted(manifest, code)
    return s


def _event(receiver_id: str = "GUARD-99", accuracy_m: float = 10) -> ShipmentEvent:
    now = datetime.now(timezone.utc)
    return ShipmentEvent(
        shipment_id="GDX1", parcel_id="P1", event_id="evt1", transaction_id="TX1",
        handoff_type="doorstep", event_timestamp=now, receipt_timestamp=now,
        handler_id="CR-1", receiver_id=receiver_id, transfer_code_ref="CODE1",
        location=LocationEvidence(latitude=3.118, longitude=101.640, accuracy_m=accuracy_m,
                                   source="device_gps", recorded_at=now),
    )


def _pass(name: str) -> CheckerResult:
    return CheckerResult(checker_name=name, status=Status.PASS, detail="ok", evidence_ids=["evt1"])


def _case(event: ShipmentEvent, checks: list[CheckerResult]) -> CaseView:
    return CaseView(
        shipment_id="GDX1", version=1, state="PENDING", event=event, checks=checks,
        decision=AgentDecision(decision="REQUEST_VERIFICATION", recommended_action="verify",
                                requires_human_approval=False, reason="unresolved"),
    )


def test_log_investigation_appends_to_real_audit_chain(store: Store):
    result = InvestigationResult(
        shipment_id="GDX1", unresolved_check="identity_authority",
        tool_call=ToolCallRecord(tool_name="lookup_delegation",
                                  arguments={"shipment_id": "GDX1", "candidate_receiver_id": "GUARD-99"},
                                  result_summary="not found", evidence_ids=["manifest:GDX1"]),
        reasoning="test reasoning", unresolved_questions=["x"], suggests_resolved=False,
    )
    log_investigation(store, result)
    with store.connect() as conn:
        trail = get_audit_trail("GDX1", conn)
    assert len(trail) == 1
    assert trail[0]["kind"] == AUDIT_KIND
    assert trail[0]["payload"]["unresolved_check"] == "identity_authority"
    assert trail[0]["payload"]["suggests_resolved"] is False


def test_unresolved_investigation_logs_but_does_not_recheck(store: Store):
    # GUARD-99 is not an authorized receiver/delegate on the seeded manifest,
    # so identity_authority is genuinely unresolved and lookup_delegation
    # will genuinely find nothing -> suggests_resolved stays False.
    case = _case(_event(receiver_id="GUARD-99"), [
        CheckerResult(checker_name="identity_authority", status=Status.UNKNOWN, detail="x", evidence_ids=["evt1"]),
        _pass("parcel_code_integrity"), _pass("location"),
    ])
    outcome = investigate_and_maybe_recheck(case, store)

    assert outcome.investigation.suggests_resolved is False
    assert outcome.audit_logged is True
    assert outcome.recheck_requested is False
    assert outcome.recheck_response is None
    assert outcome.recheck_error is None

    with store.connect() as conn:
        trail = get_audit_trail("GDX1", conn)
    assert sum(1 for e in trail if e["kind"] == AUDIT_KIND) == 1


def test_resolved_investigation_attempts_recheck_and_reports_unavailable(store: Store):
    # accuracy_m=500 makes location UNKNOWN for a weak-accuracy reason, but
    # a checkpoint of the right handoff_type genuinely exists on the
    # manifest -> lookup_checkpoint's found=True -> suggests_resolved=True.
    # No ZTRACK_API_BASE_URL/SERVICE_KEY configured (see fixture above), so
    # the recheck attempt must fail cleanly, not hang or raise.
    case = _case(_event(receiver_id="R1", accuracy_m=500), [
        _pass("identity_authority"), _pass("parcel_code_integrity"),
        CheckerResult(checker_name="location", status=Status.UNKNOWN, detail="too weak", evidence_ids=["evt1"]),
    ])
    outcome = investigate_and_maybe_recheck(case, store)

    assert outcome.investigation.suggests_resolved is True
    assert outcome.audit_logged is True
    assert outcome.recheck_requested is True
    assert outcome.recheck_response is None
    assert outcome.recheck_error is not None and "not configured" in outcome.recheck_error


def test_recheck_reason_never_contains_raw_notes(store: Store):
    """The reason sent to /recheck is templated from unresolved_check/tool
    name only (see agent.orchestrator._recheck_reason) — it must never
    carry investigation.reasoning verbatim, because reasoning can embed the
    inert, untrusted notes preview. A customer-planted secret-shaped string
    in notes must not travel over this HTTP call or land in the backend's
    own RECHECK audit entry a second time.
    """
    from agent.orchestrator import _recheck_reason
    from agent.models import ToolCallRecord as TCR

    investigation = InvestigationResult(
        shipment_id="GDX1", unresolved_check="location",
        tool_call=TCR(tool_name="lookup_checkpoint", arguments={"shipment_id": "GDX1", "handoff_type": "doorstep"},
                      result_summary="found", evidence_ids=["manifest:GDX1"]),
        reasoning="location was unresolved. TAC was 482913 (from notes). This supports resolving the check.",
        unresolved_questions=[], suggests_resolved=True,
    )
    reason = _recheck_reason(investigation)
    assert "482913" not in reason
    assert len(reason) <= 500


def test_recheck_client_wire_format_matches_backend_contract():
    """Confirms request_recheck() actually sends what backend/api.py's
    /cases/{id}/recheck + service_auth + RecheckRequest expect: path,
    X-Service-Key header, and a JSON body with exactly expected_case_version
    and reason. Uses a plain http.server stand-in rather than a live
    FastAPI app (fastapi isn't installed in every dev env this runs in),
    since this test is about the CLIENT's wire format, not the server.
    """
    captured = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            captured["path"] = self.path
            captured["headers"] = dict(self.headers)
            captured["body"] = json.loads(self.rfile.read(length))
            payload = json.dumps({"shipment_id": "GDX1", "version": 2, "state": "PENDING"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        os.environ["ZTRACK_API_BASE_URL"] = f"http://127.0.0.1:{server.server_port}"
        os.environ["ZTRACK_SERVICE_KEY"] = "sekret-service-key"
        result = request_recheck("GDX1", 1, "test reason")
    finally:
        server.shutdown()
        os.environ.pop("ZTRACK_API_BASE_URL", None)
        os.environ.pop("ZTRACK_SERVICE_KEY", None)

    assert captured["path"] == "/cases/GDX1/recheck"
    assert captured["headers"].get("X-Service-Key") == "sekret-service-key"
    assert captured["body"] == {"expected_case_version": 1, "reason": "test reason"}
    assert result == {"shipment_id": "GDX1", "version": 2, "state": "PENDING"}


def test_recheck_client_raises_when_unconfigured():
    with pytest.raises(RecheckUnavailable):
        request_recheck("GDX1", 1, "test reason")
