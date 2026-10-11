"""Deterministic verification checks, never accept claims in event.notes.

All trusted authorization/checkpoint/code records must be provided from a
server-controlled repository; an incoming ShipmentEvent is untrusted.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from math import asin, cos, radians, sin, sqrt

from backend.models import (
    CheckerResult, ShipmentEvent, Status, TransferCodeRecord, TrustedShipmentRecord,
)

# Synthetic demonstration policies, NOT actual GDEX thresholds.
MAX_LOCATION_AGE_SECONDS = 600
MAX_LOCATION_ACCURACY_M = 100.0


def _result(name: str, status: Status, detail: str, refs: list[str]) -> CheckerResult:
    return CheckerResult(checker_name=name, status=status, detail=detail, evidence_ids=refs)


def check_parcel_code_integrity(
    shipment: ShipmentEvent,
    trusted: TrustedShipmentRecord | None,
    code: TransferCodeRecord | None,
    *,
    now: datetime | None = None,
) -> CheckerResult:
    name = "parcel_code_integrity"
    if trusted is None or code is None:
        return _result(name, Status.UNKNOWN, "Trusted manifest or code record unavailable", [shipment.event_id])
    refs = [shipment.event_id, f"manifest:{trusted.shipment_id}", f"code:{code.code_ref}"]
    if shipment.parcel_id != trusted.parcel_id or shipment.shipment_id != trusted.shipment_id:
        return _result(name, Status.FAIL, "Parcel or shipment does not match trusted manifest", refs)
    if shipment.event_timestamp.tzinfo is None or shipment.receipt_timestamp.tzinfo is None:
        return _result(name, Status.UNKNOWN, "Handoff or receipt timestamp lacks timezone", refs)
    if (shipment.receipt_timestamp - shipment.event_timestamp).total_seconds() < -60:
        return _result(name, Status.FAIL, "Receipt predates handoff event beyond clock tolerance", refs)
    if (code.code_ref != shipment.transfer_code_ref or code.shipment_id != shipment.shipment_id
            or code.transaction_id != shipment.transaction_id or code.receiver_id != shipment.receiver_id):
        return _result(name, Status.FAIL, "Transfer code does not match parcel transaction and receiver", refs)
    if code.consumed_at is not None:
        return _result(name, Status.FAIL, "Transfer code already consumed (replay)", refs)
    current = now or datetime.now(timezone.utc)
    if (shipment.event_timestamp - current).total_seconds() > 60:
        return _result(name, Status.UNKNOWN, "Handoff event timestamp is unexpectedly in the future", refs)
    if code.issued_at.tzinfo is None or code.expires_at.tzinfo is None:
        return _result(name, Status.UNKNOWN, "Code timestamp lacks timezone", refs)
    if current < code.issued_at or current >= code.expires_at:
        return _result(name, Status.FAIL, "Transfer code is not active (not issued or expired)", refs)
    if trusted.custody_state != "PENDING":
        return _result(name, Status.FAIL, "Shipment custody has already been accepted", refs)
    return _result(name, Status.PASS, "Parcel and unused transaction-bound code are valid", refs)


def check_identity_authority(
    shipment: ShipmentEvent,
    trusted: TrustedShipmentRecord | None,
    authenticated_handler_id: str | None,
) -> CheckerResult:
    name = "identity_authority"
    if trusted is None or authenticated_handler_id is None:
        return _result(name, Status.UNKNOWN, "Missing trusted manifest or authenticated handler", [shipment.event_id])
    refs = [shipment.event_id, f"authority:{trusted.shipment_id}"]
    if (authenticated_handler_id != trusted.assigned_handler_id
            or authenticated_handler_id != trusted.current_custodian_id
            or shipment.handler_id != authenticated_handler_id):
        return _result(name, Status.FAIL, "Handler identity or current custody authorization mismatch", refs)
    if shipment.receiver_id not in set(trusted.authorized_receiver_ids + trusted.authorized_delegate_ids):
        return _result(name, Status.FAIL, "Receiver not listed in trusted authorization or delegation records", refs)
    return _result(name, Status.PASS, "Authenticated handler and receiver are authorized", refs)


def _distance_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    earth_radius_m = 6371000.0
    lat1, lon1, lat2, lon2 = map(radians, (a_lat, a_lon, b_lat, b_lon))
    dlat, dlon = lat2-lat1, lon2-lon1
    term = sin(dlat / 2)**2 + cos(lat1)*cos(lat2)*sin(dlon / 2)**2
    return 2 * earth_radius_m * asin(min(1.0, sqrt(term)))


def check_location_consistency(
    shipment: ShipmentEvent,
    trusted: TrustedShipmentRecord | None,
) -> CheckerResult:
    name = "location"
    if trusted is None or shipment.location is None:
        return _result(name, Status.UNKNOWN, "Missing manifest or location evidence", [shipment.event_id])
    p = shipment.location
    refs = [shipment.event_id, f"location:{shipment.event_id}"]
    if p.recorded_at.tzinfo is None or shipment.receipt_timestamp.tzinfo is None:
        return _result(name, Status.UNKNOWN, "Location or receipt timestamp lacks timezone", refs)
    if p.mock_location_flag:
        return _result(name, Status.UNKNOWN, "Device reports mock location; independent verification required", refs)
    if p.source == "unknown":
        return _result(name, Status.UNKNOWN, "Unverified location source", refs)
    age = (shipment.receipt_timestamp - p.recorded_at).total_seconds()
    if age < -60 or age > MAX_LOCATION_AGE_SECONDS:
        return _result(name, Status.UNKNOWN, "Location evidence is future-dated or stale", refs)
    if p.accuracy_m > MAX_LOCATION_ACCURACY_M:
        return _result(name, Status.UNKNOWN, "Location accuracy too weak to verify checkpoint", refs)
    allowed = [c for c in trusted.checkpoints if c.handoff_type == shipment.handoff_type]
    if not allowed:
        return _result(name, Status.UNKNOWN, "No trusted approved checkpoint for this handoff type", refs)
    for c in allowed:
        dist = _distance_m(p.latitude, p.longitude, c.latitude, c.longitude)
        if dist <= c.radius_m:
            return _result(name, Status.PASS, "Location fits a permitted checkpoint (not proof of presence)", refs + [f"checkpoint:{c.checkpoint_id}"])
    # Accuracy overlap makes it inconclusive rather than a definitive mismatch.
    if any(_distance_m(p.latitude, p.longitude, c.latitude, c.longitude) <= c.radius_m + p.accuracy_m for c in allowed):
        return _result(name, Status.UNKNOWN, "Reported accuracy overlaps checkpoint boundary", refs)
    return _result(name, Status.FAIL, "Location inconsistent with approved handoff checkpoints", refs)


def run_mandatory_checks(
    event: ShipmentEvent, trusted: TrustedShipmentRecord | None,
    code: TransferCodeRecord | None, authenticated_handler_id: str | None,
    *, now: datetime | None = None,
) -> list[CheckerResult]:
    return [
        check_parcel_code_integrity(event, trusted, code, now=now),
        check_identity_authority(event, trusted, authenticated_handler_id),
        check_location_consistency(event, trusted),
    ]
