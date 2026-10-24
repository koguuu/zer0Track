
import os
import datetime as dt

import requests
import streamlit as st

API_BASE = os.getenv("ZT_API", "http://localhost:8000").rstrip("/")
USE_MOCK = os.getenv("ZT_MOCK", "1") == "1"
SERVICE_KEY = os.getenv("ZT_SERVICE_KEY", "")
OPERATOR_ID = os.getenv("ZT_OPERATOR_ID", "")
SUPERVISOR_TOKEN = os.getenv("ZT_SUPERVISOR_TOKEN", "")
MANDATORY_CHECKERS = {"parcel_code_integrity", "identity_authority", "location"}


def _service_headers():
    return {"X-Service-Key": SERVICE_KEY} if SERVICE_KEY else {}


def login_staff(operator_id, token):
    response = requests.post(
        f"{API_BASE}/auth/login",
        json={"operator_id": operator_id, "token": token},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def verify_staff_session(session_token):
    response = requests.post(
        f"{API_BASE}/auth/verify",
        headers={"X-Session-Token": session_token},
        timeout=10,
    )
    if response.status_code == 401:
        return None
    response.raise_for_status()
    return response.json()


def logout_staff(session_token):
    response = requests.post(
        f"{API_BASE}/auth/logout",
        headers={"X-Session-Token": session_token},
        timeout=10,
    )
    if response.status_code == 401:
        return False
    response.raise_for_status()
    return response.json().get("logged_out", False)


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


def can_accept(case):
    """Mirror the backend's accept guard before offering or recording acceptance."""
    checks = case.get("checks", [])
    return (
        case.get("state", "").upper() == "PENDING"
        and case.get("decision", {}).get("decision") == "ACCEPT_READY"
        and len(checks) == 3
        and {check.get("checker_name") for check in checks} == MANDATORY_CHECKERS
        and all(check.get("status") == "PASS" for check in checks)
    )


def get_audit_trail(shipment_id):
    """Read audit entries and chain status for a case."""
    if USE_MOCK:
        return {"chain_valid": True, "events": st.session_state.get("audit_events", {}).get(shipment_id, [])}
    response = requests.get(
        f"{API_BASE}/cases/{shipment_id}/audit",
        headers=_service_headers(),
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def get_geofence(shipment_id):
    """Fetch trusted checkpoint coordinates and radii for a case."""
    if USE_MOCK:
        return {"shipment_id": shipment_id, "checkpoints": []}
    response = requests.get(
        f"{API_BASE}/cases/{shipment_id}/geofence",
        headers=_service_headers(),
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def get_investigation_count(cases):
    """Count recorded investigation events across the visible case list."""
    count = 0
    for case in cases:
        try:
            audit = get_audit_trail(case["shipment_id"])
        except requests.RequestException:
            continue
        count += sum(
            str(event.get("kind", "")).upper() == "AGENT_INVESTIGATION"
            for event in audit.get("events", [])
        )
    return count


def investigate_case(shipment_id):
    """Run one advisory investigation pass and return its result."""
    if USE_MOCK:
        case = next(c for c in _mock_store() if c["shipment_id"] == shipment_id)
        unresolved = list(case.get("decision", {}).get("unresolved_checks", []))
        investigation = {
            "shipment_id": shipment_id,
            "unresolved_check": unresolved[0] if unresolved else None,
            "tool_call": None,
            "reasoning": "Mock investigation: review the unresolved checks and supporting evidence.",
            "unresolved_questions": unresolved,
            "suggests_resolved": False,
            "injection_signal": None,
        }
        audit_events = st.session_state.setdefault("audit_events", {})
        events = audit_events.setdefault(shipment_id, [])
        events.append({"seq": len(events) + 1, "shipment_id": shipment_id,
                       "kind": "AGENT_INVESTIGATION",
                       "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                       "payload": investigation})
        return {"shipment_id": shipment_id, "case_version": case["version"],
                "audit_logged": True, "recheck_requested": False,
                "recheck_error": None, "recheck_case_version": None,
                "investigation": investigation}
    response = requests.post(
        f"{API_BASE}/cases/{shipment_id}/investigate",
        headers=_service_headers(),
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def save_approval(case, action, reason, operator, supervisor_token=None):
    if case.get("state", "").upper() != "PENDING":
        raise ValueError("Only pending cases can receive an approval decision.")
    if action == "accept" and not can_accept(case):
        raise ValueError("Accept is allowed only when all three mandatory checks pass and the backend decision is ACCEPT_READY.")
    record = {
        "action": action,
        "expected_case_version": case["version"],
        "reason": reason,
        "approval_request_id": f"dashboard-{case['shipment_id']}-{dt.datetime.now().timestamp()}",
    }
    if USE_MOCK:
        if not reason.strip():
            raise ValueError("A reason is required.")
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
    token = supervisor_token or SUPERVISOR_TOKEN
    if not token:
        raise RuntimeError("Sign in with your staff token before submitting an approval.")
    headers = {"X-Operator-Id": operator, "X-Supervisor-Token": token}
    response = requests.post(f"{API_BASE}/cases/{case['shipment_id']}/approve", json=record,
                             headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()
