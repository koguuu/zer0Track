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
import time
from collections import defaultdict
from functools import lru_cache

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from backend.models import ApprovalRequest, CaseView, RecheckRequest, ShipmentEvent
from backend.storage import BackendError, Store

app = FastAPI(title="zer0track backend", version="0.1.0")


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


# In-memory brute-force guard for supervisor login, keyed by claimed operator
# ID. Single-process only (fine for this prototype's one uvicorn worker; a
# real deployment would use a shared store). Not applied to courier/service
# auth: couriers are machine-authenticated per delivery, not a login a human
# repeatedly guesses at from a UI, and the service key is operator-invisible.
_LOGIN_ATTEMPT_LIMIT = 5
_LOGIN_LOCKOUT_SECONDS = 60
_failed_logins: dict[str, list[float]] = defaultdict(list)


def _check_lockout(principal: str) -> None:
    now = time.monotonic()
    attempts = [t for t in _failed_logins[principal] if now - t < _LOGIN_LOCKOUT_SECONDS]
    _failed_logins[principal] = attempts
    if len(attempts) >= _LOGIN_ATTEMPT_LIMIT:
        raise HTTPException(status_code=429, detail=(
            f"Too many failed login attempts for this operator ID; "
            f"try again in under {_LOGIN_LOCKOUT_SECONDS} seconds."))


def _record_failed_login(principal: str) -> None:
    _failed_logins[principal].append(time.monotonic())


def _clear_failed_logins(principal: str) -> None:
    _failed_logins.pop(principal, None)


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
    if not x_operator_id or not x_supervisor_token:
        raise HTTPException(status_code=401, detail="Invalid supervisor demo credentials")
    _check_lockout(x_operator_id)
    if not _lookup_credentials("ZTRACK_SUPERVISOR_TOKENS", x_operator_id, x_supervisor_token):
        _record_failed_login(x_operator_id)
        raise HTTPException(status_code=401, detail="Invalid supervisor demo credentials")
    _clear_failed_logins(x_operator_id)
    return x_operator_id


def service_auth(x_service_key: str | None = Header(default=None)) -> None:
    expected = os.environ.get("ZTRACK_SERVICE_KEY", "")
    if not expected or not x_service_key or not hmac.compare_digest(x_service_key, expected):
        raise HTTPException(status_code=401, detail="Invalid service credential")


@app.exception_handler(BackendError)
async def backend_error_handler(_request, exc: BackendError):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.get("/health")
def health():
    return {"status": "ok", "mode": "synthetic-prototype"}


@app.post("/auth/verify")
def verify_supervisor(operator_id: str = Depends(supervisor_auth)):
    """Confirms an X-Operator-Id/X-Supervisor-Token pair is valid, for a
    dashboard login screen to check before showing the approval UI. No case
    access, no side effects beyond the same lockout bookkeeping supervisor_auth
    already does on every call. A 401/429 here means the same call would fail
    identically on a real /approve, so the dashboard can fail fast at login
    instead of discovering a bad credential mid-decision.
    """
    return {"operator_id": operator_id, "authenticated": True}


@app.post("/cases/{shipment_id}/evaluate", response_model=CaseView)
def evaluate(shipment_id: str, event: ShipmentEvent,
             handler_id: str = Depends(courier_auth), store: Store = Depends(get_store)):
    if shipment_id != event.shipment_id:
        raise HTTPException(status_code=400, detail="Path shipment ID differs from body")
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


@app.get("/cases/{shipment_id}/audit")
def audit_trail(shipment_id: str, _auth: None = Depends(service_auth),
                store: Store = Depends(get_store)):
    from backend.audit import get_audit_trail, verify_audit_chain
    with store.connect() as conn:
        return {"chain_valid": verify_audit_chain(conn, os.environ.get("ZTRACK_AUDIT_ANCHOR_PATH")),
                "events": get_audit_trail(shipment_id, conn)}
