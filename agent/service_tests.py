"""Tests for agent/service.py (the logic behind POST /cases/{id}/investigate).

Plain unittest on purpose, so it runs without pytest or FastAPI:
    python3 -m unittest agent.service_tests -v
Also collected by pytest.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from backend.audit import get_audit_trail, verify_audit_chain
from backend.models import ShipmentEvent, TransferCodeRecord, TrustedShipmentRecord
from backend.storage import BackendError, Store

from agent.audit_log import AUDIT_KIND
from agent.service import investigate_case

DATASET = Path(__file__).resolve().parent.parent / "data" / "dataset"


def _seeded_store(tmp: str, case_id: str = "C16"):
    fixture = json.loads((DATASET / f"{case_id}.json").read_text())
    ctx = fixture["fixture_context"]
    trusted = TrustedShipmentRecord.model_validate(ctx["trusted_shipment"])
    code = TransferCodeRecord.model_validate(ctx["transfer_code"])
    event = ShipmentEvent.model_validate(fixture["shipment_event"])
    store = Store(str(Path(tmp) / "svc.sqlite"))
    store.seed_trusted(trusted, code)
    case = store.evaluate_case(event, ctx["authenticated_handler_id"])
    return store, case


class InvestigateCaseTests(unittest.TestCase):
    def setUp(self):
        self._old_key = os.environ.get("ZTRACK_AUDIT_HMAC_KEY")
        os.environ["ZTRACK_AUDIT_HMAC_KEY"] = "service-tests-key-at-least-32-characters"
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def tearDown(self):
        if self._old_key is None:
            os.environ.pop("ZTRACK_AUDIT_HMAC_KEY", None)
        else:
            os.environ["ZTRACK_AUDIT_HMAC_KEY"] = self._old_key

    def test_writes_agent_investigation_entry_and_chain_stays_valid(self):
        store, case = _seeded_store(self._tmp.name)
        result = investigate_case(store, case.shipment_id, use_env_llm=False)

        self.assertTrue(result["audit_logged"])
        self.assertEqual(result["case_version"], case.version)
        self.assertIsNone(result["investigation"]["injection_signal"])  # no LLM client
        with store.connect() as conn:
            trail = get_audit_trail(case.shipment_id, conn)
            self.assertTrue(verify_audit_chain(conn))
        kinds = [e["kind"] for e in trail]
        self.assertEqual(kinds.count(AUDIT_KIND), 1)
        logged = next(e for e in trail if e["kind"] == AUDIT_KIND)["payload"]
        self.assertEqual(logged, result["investigation"])

    def test_does_not_change_checks_decision_or_version(self):
        store, case = _seeded_store(self._tmp.name)
        before = store.get_case(case.shipment_id).model_dump(mode="json")
        investigate_case(store, case.shipment_id, use_env_llm=False)
        after = store.get_case(case.shipment_id).model_dump(mode="json")
        self.assertEqual(before, after)

    def test_each_call_appends_one_more_entry(self):
        store, case = _seeded_store(self._tmp.name)
        investigate_case(store, case.shipment_id, use_env_llm=False)
        investigate_case(store, case.shipment_id, use_env_llm=False)
        with store.connect() as conn:
            kinds = [e["kind"] for e in get_audit_trail(case.shipment_id, conn)]
        self.assertEqual(kinds.count(AUDIT_KIND), 2)

    def test_unknown_shipment_is_404_and_writes_nothing(self):
        store, _case = _seeded_store(self._tmp.name)
        with store.connect() as conn:
            before = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        with self.assertRaises(BackendError) as ctx:
            investigate_case(store, "NO-SUCH-SHIPMENT", use_env_llm=False)
        self.assertEqual(ctx.exception.status_code, 404)
        with store.connect() as conn:
            after = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
