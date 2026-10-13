
import os
import datetime as dt

import requests
import streamlit as st

API_BASE = os.getenv("ZT_API", "http://localhost:8000").rstrip("/")
USE_MOCK = os.getenv("ZT_MOCK", "1") == "1"
SERVICE_KEY = os.getenv("ZT_SERVICE_KEY", "")
OPERATOR_ID = os.getenv("ZT_OPERATOR_ID", "")
SUPERVISOR_TOKEN = os.getenv("ZT_SUPERVISOR_TOKEN", "")


def _service_headers():
    return {"X-Service-Key": SERVICE_KEY} if SERVICE_KEY else {}


def _check(name, status, detail, evidence_ids=(), confidence=None):
    passed = {"PASS": True, "FAIL": False, "UNKNOWN": None}[status]
    return {
        "checker_name": name,
        "status": status,
        "passed": passed,
        "confidence": confidence,
        "detail": detail,
        "evidence_ids": list(evidence_ids),
    }


def _case(shipment_id, state, handler, location, checks, decision, approval=None):
    now = "2026-10-13T10:00:00+08:00"
    return {
        "shipment_id": shipment_id,
        "version": 1,
        "state": state,
        "event": {
            "shipment_id": shipment_id,
            "parcel_id": f"P-{shipment_id}",
            "event_id": f"EV-{shipment_id}",
            "transaction_id": f"TX-{shipment_id}",
            "handoff_type": "guardhouse",
            "event_timestamp": now,
            "receipt_timestamp": now,
            "handler_id": handler,
            "receiver_id": "R-77",
            "transfer_code_ref": f"CODE-{shipment_id}",
            "location": {
                "latitude": 3.139,
                "longitude": 101.6869,
                "accuracy_m": 12,
                "source": "synthetic",
                "recorded_at": now,
                "mock_location_flag": False,
            },
            "notes": f"Synthetic handoff at {location}.",
            "photo_metadata_ref": None,
            "signed_receipt_status": "present_unverified",
        },
        "checks": checks,
        "decision": decision,
        "approval": approval,
    }


MOCK_CASES = [
    _case("GDX-1001", "accepted", "H-204", "Condo guardhouse", [
        _check("parcel_code_integrity", "PASS", "Scan matches manifest; code valid and unused.", ["EV-88"], .98),
        _check("identity_authority", "PASS", "Delegate D-77 is authorised by receiver.", ["EV-89"], .90),
        _check("location", "PASS", "Guardhouse is a registered drop point.", ["EV-90"], .85),
    ], {"decision": "ACCEPT_READY", "recommended_action": "Accept after policy revalidation",
        "requires_human_approval": False, "reason": "All mandatory checks passed.", "unresolved_checks": [], "evidence_ids": ["EV-88", "EV-89", "EV-90"]}),
    _case("GDX-1002", "pending", "H-204", "Condo guardhouse", [
        _check("parcel_code_integrity", "PASS", "Scan matches manifest; code valid and unused.", ["EV-91"], .98),
        _check("identity_authority", "UNKNOWN", "No delegation record found.", ["EV-92"], .40),
        _check("location", "UNKNOWN", "No authorisation confirms this drop point.", ["EV-93"], .50),
    ], {"decision": "REQUEST_VERIFICATION", "recommended_action": "Request recipient verification",
        "requires_human_approval": False, "reason": "Identity and drop point remain unresolved.", "unresolved_checks": ["identity_authority", "location"], "evidence_ids": ["EV-91", "EV-92", "EV-93"]}),
    _case("GDX-1003", "escalated", "H-310", "Unit 12-4 door", [
        _check("parcel_code_integrity", "FAIL", "Transfer code already consumed (replay).", ["EV-94"], .99),
        _check("identity_authority", "PASS", "Handler matches assigned custodian.", ["EV-95"], .88),
        _check("location", "PASS", "Location matches a permitted point.", ["EV-96"], .80),
    ], {"decision": "ESCALATE", "recommended_action": "Escalate for investigation",
        "requires_human_approval": True, "reason": "Transfer code replay detected.", "unresolved_checks": [], "evidence_ids": ["EV-94"]}),
    _case("GDX-1004", "accepted", "H-118", "Depot counter", [
        _check("parcel_code_integrity", "PASS", "Scan matches manifest; code valid.", ["EV-97"], .97),
        _check("identity_authority", "PASS", "Receiver ID matches manifest.", ["EV-98"], .95),
        _check("location", "PASS", "Depot is an approved collection point.", ["EV-99"], .99),
    ], {"decision": "ACCEPT_READY", "recommended_action": "Accept after policy revalidation",
        "requires_human_approval": False, "reason": "All mandatory checks passed.", "unresolved_checks": [], "evidence_ids": ["EV-97", "EV-98", "EV-99"]}),
    _case("GDX-1005", "flagged", "H-251", "Warehouse dock 3", [
        _check("parcel_code_integrity", "PASS", "Scan matches manifest.", ["EV-100"], .96),
        _check("identity_authority", "UNKNOWN", "Custody history lookup did not return.", ["EV-101"], .30),
        _check("location", "PASS", "Location is consistent with dock 3.", ["EV-102"], .82),
    ], {"decision": "RECOMMEND_HOLD", "recommended_action": "Operator review; do not auto-accept",
        "requires_human_approval": False, "reason": "Identity check could not be completed.", "unresolved_checks": ["identity_authority"], "evidence_ids": ["EV-101"]}),
]


def _mock_store():
    if "cases" not in st.session_state:
        st.session_state.cases = [dict(c) for c in MOCK_CASES]
    return st.session_state.cases


def get_cases():
    if USE_MOCK:
        cases = _mock_store()
    else:
        response = requests.get(f"{API_BASE}/cases", headers=_service_headers(), timeout=10)
        response.raise_for_status()
        cases = response.json()
    # Keep the backend state intact and add a dashboard filter/count alias.
    state_alias = {
        "PENDING": "pending", "ACCEPTED": "accepted", "HELD": "flagged",
        "REJECTED": "rejected", "ESCALATED": "escalated",
    }
    return [dict(case, status=state_alias.get(case["state"], case["state"].lower())) for case in cases]


def get_case(shipment_id):
    if USE_MOCK:
        return next(c for c in _mock_store() if c["shipment_id"] == shipment_id)
    response = requests.get(f"{API_BASE}/cases/{shipment_id}", headers=_service_headers(), timeout=10)
    response.raise_for_status()
    return response.json()


def save_approval(case, action, reason, operator):
    record = {
        "action": action,
        "expected_case_version": case["version"],
        "reason": reason,
        "approval_request_id": f"dashboard-{case['shipment_id']}-{dt.datetime.now().timestamp()}",
    }
    if USE_MOCK:
        approval = {
            "approval_id": record["approval_request_id"],
            "shipment_id": case["shipment_id"],
            "evidence_version": case["version"],
            "operator_id": operator,
            "action": action,
            "reason": reason,
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        case["approval"] = approval
        case["state"] = {"accept": "accepted", "hold": "flagged",
                          "reject": "rejected", "escalate": "escalated"}[action]
        return approval
    if not SUPERVISOR_TOKEN:
        raise RuntimeError("Set ZT_SUPERVISOR_TOKEN to submit an approval to the backend.")
    headers = {"X-Operator-Id": operator, "X-Supervisor-Token": SUPERVISOR_TOKEN}
    response = requests.post(f"{API_BASE}/cases/{case['shipment_id']}/approve", json=record,
                             headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()
