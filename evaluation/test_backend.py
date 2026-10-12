"""Raja's compatibility tests against the actual supplied Bhone backend.

Backend tests require backend/ and Pydantic v2. All DB writes use a temporary
synthetic test database; no production endpoint or approval is contacted.
"""

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from data.generate_data import NOW, export_dataset, has_private_labels
from evaluation.metrics import compute_metrics
from evaluation.run_eval import evaluate_with_bhone, load_dataset, run_rules_only, run_rules_and_llm


class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from backend.models import ShipmentEvent, CourierHistory, TrustedShipmentRecord, TransferCodeRecord, ApprovalRequest
            from backend.storage import Store, BackendError
            from backend.audit import get_audit_trail, verify_audit_chain
        except ModuleNotFoundError as exc:
            if exc.name in ("backend", "backend.models"):
                raise unittest.SkipTest("Add Bhone's actual backend/ to run backend compatibility tests.") from exc
            raise
        cls.models = {item.__name__: item for item in
                      (ShipmentEvent, CourierHistory, TrustedShipmentRecord, TransferCodeRecord, ApprovalRequest)}
        cls.Store, cls.BackendError = Store, BackendError
        cls.audit_trail, cls.verify_chain = staticmethod(get_audit_trail), staticmethod(verify_audit_chain)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        export_dataset(self.root / "cases", labels_path=self.root / "labels.json", require_team_schema=True)
        self.dataset = load_dataset(self.root / "cases", self.root / "labels.json")
        self.fixtures = {row["input"]["case_id"]: row["input"] for row in self.dataset}
        env = patch.dict(os.environ, {"ZTRACK_AUDIT_HMAC_KEY": "SYNTHETIC-RAJA-TEST-KEY-DO-NOT-DEPLOY-12345"})
        env.start()
        self.addCleanup(env.stop)

    def _model(self, name, payload):
        return self.models[name].model_validate(payload)

    def _fresh(self, case_id="C01"):
        # Store uses the real server clock. Shift every synthetic timestamp
        # together so normal records do not expire on a later test run.
        delta = datetime.now(timezone.utc) - datetime.fromisoformat(NOW.replace("Z", "+00:00"))
        def shift(value):
            if isinstance(value, dict):
                return {key: shift(item) for key, item in value.items()}
            if isinstance(value, list):
                return [shift(item) for item in value]
            if isinstance(value, str) and value.startswith("2026-10-11T"):
                return (datetime.fromisoformat(value.replace("Z", "+00:00")) + delta).isoformat()
            return value
        return shift(copy.deepcopy(self.fixtures[case_id]))

    def _store(self, case_id="C01"):
        fixture = self._fresh(case_id)
        store = self.Store(str(self.root / (case_id + ".sqlite")))
        context = fixture["fixture_context"]
        store.seed_trusted(self._model("TrustedShipmentRecord", context["trusted_shipment"]),
                           self._model("TransferCodeRecord", context["transfer_code"]))
        event = self._model("ShipmentEvent", fixture["shipment_event"])
        return store, event, context

    def _approval(self, case, request_id, *, version=None):
        return self._model("ApprovalRequest", {
            "action": "accept", "expected_case_version": version or case.version,
            "reason": "Synthetic supervisor test approval", "approval_request_id": request_id})

    def test_all_twenty_events_histories_and_trusted_seeds_validate(self):
        for fixture in self.fixtures.values():
            self.assertFalse(has_private_labels(fixture))
            self._model("ShipmentEvent", fixture["shipment_event"])
            self._model("CourierHistory", fixture["courier_history"])
            self._model("TrustedShipmentRecord", fixture["fixture_context"]["trusted_shipment"])
            if fixture["fixture_context"]["transfer_code"] is not None:
                self._model("TransferCodeRecord", fixture["fixture_context"]["transfer_code"])
            self.assertEqual(fixture["schema_validation"]["shipment_event"], "model_validated")
            self.assertFalse(fixture["schema_validation"]["schema_lock_confirmed"])

    def test_rules_observations_and_false_alerts_are_not_hidden(self):
        rows = run_rules_only(self.dataset, pipeline=evaluate_with_bhone)
        metrics = compute_metrics(rows)
        self.assertEqual(metrics["evaluated_cases"], 20)
        self.assertEqual(metrics["flagged_invalid_proofs"], 13)
        self.assertEqual(metrics["missed_case_ids"], ["C20"])
        self.assertEqual(metrics["false_alerts"], 2)
        self.assertEqual(metrics["unsupported_acceptances"], 0)
        self.assertEqual(metrics["protocol_violation_cases"], 0)
        self.assertTrue(all(row["expected_action"] == row["observed_action"] for row in rows))

    def test_registered_guardhouse_exception_passes_but_missing_checkpoint_does_not(self):
        rows = {row["case_id"]: row for row in run_rules_only(self.dataset, pipeline=evaluate_with_bhone)}
        self.assertEqual(rows["C14"]["observed_action"], "ACCEPT")
        self.assertEqual(rows["C15"]["checks"], {"parcel_code": "PASS", "identity": "PASS", "location": "UNKNOWN"})
        self.assertEqual(rows["C15"]["observed_action"], "VERIFY")

    def test_phished_code_remains_receiver_bound_and_spoof_signal_is_unknown(self):
        rows = {row["case_id"]: row for row in run_rules_only(self.dataset, pipeline=evaluate_with_bhone)}
        self.assertEqual(rows["C10"]["checks"]["parcel_code"], "FAIL")
        # Bhone's checkers.py deliberately fails identity closed as UNKNOWN
        # rather than FAIL for an unlisted/unverifiable receiver (an absent
        # delegation record isn't proof of denial) -- policy.py still routes
        # UNKNOWN identity to the same RECOMMEND_HOLD/requires_approval=True
        # outcome FAIL did, so the phished-code scenario this test is named
        # for is still caught; only the check-level label changed.
        self.assertEqual(rows["C10"]["checks"]["identity"], "UNKNOWN")
        self.assertEqual(rows["C04"]["checks"]["location"], "UNKNOWN")

    def test_injection_text_cannot_override_mandatory_rules(self):
        rows = {row["case_id"]: row for row in run_rules_only(self.dataset, pipeline=evaluate_with_bhone)}
        for case_id, action in (("C16", "HOLD"), ("C17", "VERIFY"), ("C18", "HOLD")):
            self.assertEqual(rows[case_id]["observed_action"], action)
            self.assertFalse(rows[case_id]["notes_exposed"])
        self.assertIn("SYSTEM:", self.fixtures["C18"]["shipment_event"]["notes"])
        # This verifies deterministic rules only; a real agent injection test is pending.

    def test_bridge_does_not_mutate_inputs_or_execute_an_approval(self):
        original = copy.deepcopy(self.dataset)
        rows = run_rules_only(self.dataset, pipeline=evaluate_with_bhone)
        self.assertEqual(self.dataset, original)
        self.assertTrue(all(not row["executed"] and not row["agent_invoked"] and not row["llm_used"] for row in rows))
        self.assertTrue(all(row["backend_decision"] and row["backend_checks"] for row in rows))
        with self.assertRaises(ValueError):
            evaluate_with_bhone(fixture=self.fixtures["C01"], mode="rules_only", policy_config={}, evaluation_only=False)

    def test_real_llm_arm_stays_pending_without_kogu(self):
        rows = run_rules_and_llm(self.dataset, pipeline=evaluate_with_bhone)
        self.assertTrue(all(row["status"] == "awaiting_pipeline" and row["observed_action"] is None for row in rows))
        self.assertTrue(all(row["latency_ms"] is None for row in rows))
        self.assertIsNone(compute_metrics(rows)["invalid_proof_detection_percent"])

    def test_local_accept_consumes_code_once_and_audit_verifies(self):
        store, event, context = self._store()
        case = store.evaluate_case(event, context["authenticated_handler_id"])
        self.assertEqual(case.state, "PENDING")
        self.assertEqual(case.decision.decision, "ACCEPT_READY")
        request = self._approval(case, "SYN-APPROVAL-01")
        accepted = store.approve_case(event.shipment_id, request, "SYN-OP01")
        self.assertEqual(accepted.state, "ACCEPTED")
        retry = store.approve_case(event.shipment_id, request, "SYN-OP01")
        self.assertEqual(retry.approval.approval_id, accepted.approval.approval_id)
        with store.connect() as conn:
            self.assertIsNotNone(store._code(conn, event.transfer_code_ref).consumed_at)
            self.assertEqual(store._trusted(conn, event.shipment_id).custody_state, "ACCEPTED")
            self.assertTrue(self.verify_chain(conn))
            self.assertEqual([row["kind"] for row in self.audit_trail(event.shipment_id, conn)], ["EVALUATION", "APPROVAL"])

    def test_failed_identity_cannot_be_accepted_by_supervisor(self):
        store, event, context = self._store("C09")
        case = store.evaluate_case(event, context["authenticated_handler_id"])
        with self.assertRaises(self.BackendError) as error:
            store.approve_case(event.shipment_id, self._approval(case, "SYN-BAD-ACCEPT"), "SYN-OP01")
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(store.get_case(event.shipment_id).state, "PENDING")
        with store.connect() as conn:
            self.assertIsNone(store._code(conn, event.transfer_code_ref).consumed_at)

    def test_stale_case_version_and_changed_event_replay_are_rejected(self):
        store, event, context = self._store()
        first = store.evaluate_case(event, context["authenticated_handler_id"])
        changed = event.model_copy(update={"notes": "Synthetic new evidence"})
        with self.assertRaises(self.BackendError):
            store.evaluate_case(changed, context["authenticated_handler_id"])
        changed = event.model_copy(update={"event_id": "SYN-E-NEW", "notes": "Synthetic new evidence"})
        second = store.evaluate_case(changed, context["authenticated_handler_id"])
        self.assertEqual(second.version, 2)
        with self.assertRaises(self.BackendError) as error:
            store.approve_case(event.shipment_id, self._approval(first, "SYN-STALE"), "SYN-OP01")
        self.assertEqual(error.exception.status_code, 409)

    def test_evaluation_retry_is_idempotent_but_changed_authenticated_handler_is_rejected(self):
        store, event, context = self._store()
        first = store.evaluate_case(event, context["authenticated_handler_id"])
        second = store.evaluate_case(event, context["authenticated_handler_id"])
        self.assertEqual(first.version, second.version)
        with self.assertRaises(self.BackendError) as error:
            store.evaluate_case(event, "SYN-H99")
        self.assertEqual(error.exception.status_code, 403)

    def test_two_concurrent_accepts_have_one_winner(self):
        store, event, context = self._store()
        case = store.evaluate_case(event, context["authenticated_handler_id"])
        def approve(number):
            try:
                return store.approve_case(event.shipment_id, self._approval(case, f"SYN-RACE-{number}"), "SYN-OP01").state
            except self.BackendError as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            observed = list(pool.map(approve, (1, 2)))
        self.assertCountEqual(observed, ["ACCEPTED", 409])


if __name__ == "__main__":
    unittest.main()
