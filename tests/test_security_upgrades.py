"""Regression checks on temporary synthetic data; do not touch team evaluation outputs."""
import asyncio
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
from backend.api import app, get_store
from backend.audit import export_checkpoint, verify_audit_chain
from backend.models import ApprovalRequest, ShipmentEvent, TrustedShipmentRecord, TransferCodeRecord
from backend.storage import BackendError, Store
from scripts.audit_security_demo import demonstrate

ROOT = Path(__file__).resolve().parents[1]


class SecurityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        secrets = patch.dict(os.environ, {
            "ZTRACK_AUDIT_HMAC_KEY": "DEMO-ONLY-AUDIT-SECRET-0123456789ABCDEF",
            "ZTRACK_SERVICE_KEY": "demo-service-key",
            "ZTRACK_COURIER_TOKENS": '{"SYN-H01":"courier-secret"}',
            "ZTRACK_SUPERVISOR_TOKENS": '{"OP1":"pw1","OP2":"pw2"}',
        })
        secrets.start()
        self.addCleanup(secrets.stop)
        self.store = Store(str(self.root / "db.sqlite"))
        fixture = json.loads((ROOT / "data/dataset/C01.json").read_text())
        shift = datetime.now(timezone.utc) - datetime.fromisoformat("2026-10-11T04:00:00Z".replace("Z", "+00:00"))
        def update_time(data):
            if isinstance(data, dict):
                return {key: update_time(value) for key, value in data.items()}
            if isinstance(data, list):
                return [update_time(item) for item in data]
            if isinstance(data, str) and data.startswith('2026-10-11T'):
                return (datetime.fromisoformat(data.replace('Z', '+00:00')) + shift).isoformat()
            return data
        fixture = update_time(fixture)
        context = fixture["fixture_context"]
        self.event = ShipmentEvent.model_validate(fixture["shipment_event"])
        self.store.seed_trusted(TrustedShipmentRecord.model_validate(context["trusted_shipment"]),
                                TransferCodeRecord.model_validate(context["transfer_code"]))
        self.case = self.store.evaluate_case(self.event, "SYN-H01")

    def approval(self, operator, action, request_id):
        return self.store.approve_case(self.case.shipment_id, ApprovalRequest(
            action=action, expected_case_version=self.case.version,
            reason="Verified by supervisor", approval_request_id=request_id), operator)

    def test_dual_approval_requires_separate_operator(self):
        first = self.approval("OP1", "reject", "req-1")
        self.assertEqual(first.state, "PENDING")
        with self.assertRaises(BackendError) as exc:
            self.approval("OP1", "reject", "req-2")
        self.assertEqual(exc.exception.status_code, 409)
        second = self.approval("OP2", "reject", "req-3")
        self.assertEqual(second.state, "REJECTED")
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM pending_dual_approvals").fetchone()[0], 0)
            self.assertTrue(verify_audit_chain(conn))

    def test_dual_approval_cannot_cross_actions_or_versions(self):
        self.assertEqual(self.approval("OP1", "reject", "req-r").state, "PENDING")
        self.assertEqual(self.approval("OP2", "escalate", "req-e").state, "PENDING")
        from backend.models import RecheckRequest
        self.store.recheck_case(self.case.shipment_id, RecheckRequest(
            expected_case_version=self.case.version, reason="Fresh evidence requested"))
        with self.assertRaises(BackendError):
            self.approval("OP2", "reject", "req-stale")
        with self.store.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM pending_dual_approvals").fetchone()[0], 0)

    def test_rate_limit_per_courier(self):
        for _ in range(10):
            self.store.enforce_courier_rate_limit("SYN-H01")
        self.store.enforce_courier_rate_limit("OTHER")
        with self.assertRaises(BackendError) as err:
            self.store.enforce_courier_rate_limit("SYN-H01")
        self.assertEqual(err.exception.status_code, 429)

    def test_geofence_and_auth(self):
        app.dependency_overrides[get_store] = lambda: self.store
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                bad = await client.get(f"/cases/{self.case.shipment_id}/geofence")
                self.assertEqual(bad.status_code, 401)
                headers = {"x-service-key": "demo-service-key"}
                good = await client.get(f"/cases/{self.case.shipment_id}/geofence", headers=headers)
                self.assertEqual(good.status_code, 200, good.text)
                item = good.json()
                self.assertEqual(item["checkpoints"][0]["checkpoint_id"], "MAIN")
                self.assertEqual(item["checkpoints"][0]["radius_m"], 80.0)
                self.assertEqual(item["checkpoints"][0]["distance_m"], 0)
                self.assertTrue(item["checkpoints"][0]["within_radius"])
                missing = await client.get("/cases/NOPE/geofence", headers=headers)
                self.assertEqual(missing.status_code, 404)
        try:
            asyncio.run(run())
        finally:
            app.dependency_overrides.clear()

    def test_evaluate_endpoint_limited_after_ten(self):
        app.dependency_overrides[get_store] = lambda: self.store
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                h = {"x-courier-id": "SYN-H01", "x-courier-token": "courier-secret"}
                for _ in range(10):
                    result = await client.post(f"/cases/{self.case.shipment_id}/evaluate", json=self.event.model_dump(mode="json"), headers=h)
                    self.assertEqual(result.status_code, 200, result.text)
                blocked = await client.post(f"/cases/{self.case.shipment_id}/evaluate", json=self.event.model_dump(mode="json"), headers=h)
                self.assertEqual(blocked.status_code, 429)
        try:
            asyncio.run(run())
        finally:
            app.dependency_overrides.clear()

    def test_anchor_and_isolated_tamper(self):
        before, after = demonstrate(self.store.path)
        self.assertTrue(before)
        self.assertFalse(after)
        anchor = self.root / "other-store" / "anchor.json"
        with self.store.connect() as conn:
            content = export_checkpoint(conn, str(anchor))
            self.assertGreater(content["seq"], 0)
            self.assertTrue(verify_audit_chain(conn, str(anchor)))
        anchor.write_text(anchor.read_text().replace('"seq":1', '"seq":999'))
        with self.store.connect() as conn:
            # tampering with independent checkpoint must be detected
            self.assertFalse(verify_audit_chain(conn, str(anchor)))


if __name__ == "__main__":
    unittest.main()
