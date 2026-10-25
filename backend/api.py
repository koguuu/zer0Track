"""FastAPI endpoint layer for zer0track's simulated-delivery prototype.

Endpoints in team's outline:
POST /cases/{shipment_id}/evaluate
POST /cases/{shipment_id}/approve
POST /cases/{shipment_id}/investigate   (service key; advisory agent pass)
GET  /cases/{shipment_id}

All credentials here are SYNTHETIC demo secrets supplied via environment
variables. Replace this mock authenticator with real IAM for production.
"""
from __future__ import annotations

import hmac
import json
import os
from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from backend.models import ApprovalRequest, CaseView, RecheckRequest, ShipmentEvent
from backend.storage import BackendError, Store

app = FastAPI(title="zer0track backend", version="0.1.0", docs_url=None, redoc_url=None, openapi_url=None)


@lru_cache(maxsize=1)
def get_store() -> Store:
    return Store(os.environ.get("ZTRACK_DB_PATH", "./var/zer0track.sqlite"))


def _lookup_credentials(env_key: str, principal: str, secret: str) -> bool:
    try:
        credentials = json.loads(os.environ.get(env_key, "{}"))
    except json.JSONDecodeError:
        return False
    correct = credentials.get(principal)
    return bool(isinstance(correct, str) and correct and hmac.compare_digest(correct, secret))


def courier_auth(
    x_courier_id: str | None = Header(default=None),
    x_courier_token: str | None = Header(default=None),
) -> str:
    if not x_courier_id or not x_courier_token or not _lookup_credentials(
        "ZTRACK_COURIER_TOKENS", x_courier_id, x_courier_token
    ):
        raise HTTPException(status_code=401, detail="Invalid courier demo credentials")
    return x_courier_id


def supervisor_auth(
    x_operator_id: str | None = Header(default=None),
    x_supervisor_token: str | None = Header(default=None),
) -> str:
    if not x_operator_id or not x_supervisor_token or not _lookup_credentials(
        "ZTRACK_SUPERVISOR_TOKENS", x_operator_id, x_supervisor_token
    ):
        raise HTTPException(status_code=401, detail="Invalid supervisor demo credentials")
    return x_operator_id


def service_auth(x_service_key: str | None = Header(default=None)) -> None:
    expected = os.environ.get("ZTRACK_SERVICE_KEY", "")
    if not expected or not x_service_key or not hmac.compare_digest(x_service_key, expected):
        raise HTTPException(status_code=401, detail="Invalid service credential")


@app.exception_handler(BackendError)
async def backend_error_handler(_request, exc: BackendError):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})



from pydantic import BaseModel
from backend.staff_auth import verify_staff, create_staff_session, verify_staff_session, revoke_staff_session


class StaffLoginRequest(BaseModel):
    operator_id: str
    token: str


@app.post("/auth/login")
def staff_login(request: StaffLoginRequest, store: Store = Depends(get_store)):
    result = verify_staff(store, request.operator_id, request.token)

    if result == "locked":
        raise HTTPException(
            status_code=429,
            detail="Account temporarily locked"
        )

    if result != "ok":
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials"
        )

    session = create_staff_session(store, request.operator_id.strip())
    return {
        "authenticated": True,
        **session
    }



@app.post("/auth/verify")
def verify_login(
    x_session_token: str | None = Header(default=None),
    store: Store = Depends(get_store),
):
    operator_id = verify_staff_session(store, x_session_token or "")
    if operator_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return {"authenticated": True, "operator_id": operator_id}



@app.post("/auth/logout")
def staff_logout(
    x_session_token: str | None = Header(default=None),
    store: Store = Depends(get_store),
):
    if not x_session_token:
        raise HTTPException(status_code=401, detail="Missing session token")

    operator_id = verify_staff_session(store, x_session_token)

    if operator_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    revoke_staff_session(store, x_session_token)

    return {"logged_out": True}


@app.get("/health")
def health():
    return {"status": "ok", "mode": "synthetic-prototype"}


@app.post("/cases/{shipment_id}/evaluate", response_model=CaseView)
def evaluate(shipment_id: str, event: ShipmentEvent,
             handler_id: str = Depends(courier_auth), store: Store = Depends(get_store)):
    if shipment_id != event.shipment_id:
        raise HTTPException(status_code=400, detail="Path shipment ID differs from body")
    store.enforce_courier_rate_limit(handler_id)
    return store.evaluate_case(event, handler_id)


@app.post("/cases/{shipment_id}/recheck", response_model=CaseView)
def recheck(shipment_id: str, request: RecheckRequest,
            _auth: None = Depends(service_auth), store: Store = Depends(get_store)):
    """Authenticated internal re-evaluation of the latest trusted evidence.

    The AI may request this via a controlled service. Its own findings cannot
    set check results to PASS and may not supply arbitrary trusted data.
    """
    return store.recheck_case(shipment_id, request)


@app.post("/cases/{shipment_id}/investigate")
def investigate(shipment_id: str, _auth: None = Depends(service_auth),
                store: Store = Depends(get_store)):
    """Runs one advisory agent investigation and appends it to the audit chain
    (kind AGENT_INVESTIGATION). Never changes checks, the decision or the case
    version; any recheck it requests goes through the normal /recheck rules.
    Imported lazily because agent.* imports backend.*.
    """
    from agent.service import investigate_case
    return investigate_case(store, shipment_id)


@app.post("/cases/{shipment_id}/approve", response_model=CaseView)
def approve(shipment_id: str, request: ApprovalRequest,
            operator_id: str = Depends(supervisor_auth), store: Store = Depends(get_store)):
    return store.approve_case(shipment_id, request, operator_id)


@app.get("/cases", response_model=list[CaseView])
def list_cases(state: str | None = None, limit: int = 100,
               _auth: None = Depends(service_auth), store: Store = Depends(get_store)):
    """Read-only list for dashboards. ?state=PENDING filters to one state;
    omit it to list all cases, newest first. Never includes anything an
    evaluate/recheck/approve call wouldn't already have exposed."""
    return store.list_cases(state=state, limit=limit)


@app.get("/cases/{shipment_id}", response_model=CaseView)
def get_case(shipment_id: str, _auth: None = Depends(service_auth),
             store: Store = Depends(get_store)):
    return store.get_case(shipment_id)



@app.get("/cases/{shipment_id}/geofence")
def get_geofence(
    shipment_id: str,
    _auth: None = Depends(service_auth),
    store: Store = Depends(get_store),
):
    """Return approved checkpoint areas for the dashboard map."""
    case = store.get_case(shipment_id)

    with store.connect() as conn:
        trusted = store._trusted(conn, shipment_id)

    checkpoints = []
    if trusted is not None:
        for checkpoint in trusted.checkpoints:
            if checkpoint.handoff_type == case.event.handoff_type:
                checkpoints.append({
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "latitude": checkpoint.latitude,
                    "longitude": checkpoint.longitude,
                    "radius_m": checkpoint.radius_m,
                })

    from backend.checkers import _distance_m
    location = case.event.location
    for cp in checkpoints:
        distance = (_distance_m(location.latitude, location.longitude,
                                cp["latitude"], cp["longitude"])
                    if location is not None else None)
        cp["distance_m"] = round(distance, 2) if distance is not None else None
        cp["within_radius"] = (distance <= cp["radius_m"] if distance is not None else None)
    return {
        "shipment_id": shipment_id,
        "handoff_type": case.event.handoff_type,
        "checkpoints": checkpoints,
        "reported_location": (location.model_dump(mode="json") if location else None),
        "location_check_status": next((c.status.value for c in case.checks
                                       if c.checker_name == "location"), "UNKNOWN"),
    }


@app.get("/cases/{shipment_id}/approvals/pending")
def pending_approvals(shipment_id: str, _auth: None = Depends(service_auth),
                      store: Store = Depends(get_store)):
    """Read-only UI state for independent second-supervisor signoff."""
    return {"shipment_id": shipment_id, "approvals": store.pending_approvals(shipment_id)}


@app.get("/cases/{shipment_id}/audit")
def audit_trail(shipment_id: str, _auth: None = Depends(service_auth),
                store: Store = Depends(get_store)):
    from backend.audit import get_audit_trail, verify_audit_chain
    with store.connect() as conn:
        return {"chain_valid": verify_audit_chain(conn, os.environ.get("ZTRACK_AUDIT_ANCHOR_PATH")),
                "events": get_audit_trail(shipment_id, conn)}


@app.get("/cases/{shipment_id}/audit/tamper-demo")
def audit_tamper_demo(shipment_id: str, seq: int,
                      _auth: None = Depends(service_auth), store: Store = Depends(get_store)):
    """Read-only 'what if this entry were edited?' demo for the hash-chain
    visualizer. Recomputes the chain in memory with one entry's payload
    swapped; never writes anything to the real audit log."""
    from backend.audit import simulate_tamper
    fake_payload = {"demo": True, "note": "This payload is a what-if shown only by this "
                                           "read-only endpoint; nothing was written."}
    with store.connect() as conn:
        try:
            return simulate_tamper(conn, shipment_id, seq, fake_payload)
        except ValueError as error:
            raise HTTPException(status_code=404, detail=str(error))

