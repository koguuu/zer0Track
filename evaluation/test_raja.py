"""Tests for Raja's generator/harness/metrics; no backend or agent is shipped.

Response doubles below test the adapter contract. They are not comparators,
fraud detectors or a real LLM, and are never used to create the sample report.
"""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from data.generate_data import LABELS, export_dataset, generate_courier_history, generate_shipment, has_private_labels, main as generate_main
from evaluation.metrics import compute_detection_rate, compute_false_alert_rate, compute_metrics, compute_time_to_recommendation
from evaluation.policy_variants import POLICY_VARIANTS, policy_config, run_variant
from evaluation.run_eval import load_dataset, main, run_baseline, run_rules_only, run_rules_and_llm, write_results


def _response(policy, *, action="ACCEPT", checks=None):
    return {"action": action, "checks": checks or {"parcel_code": "PASS", "identity": "PASS", "location": "PASS"},
            "tool_calls": [], "executed": False, "requires_human_approval": action in ("HOLD", "ESCALATE", "REJECT"),
            "llm_used": False, "agent_invoked": False, "notes_exposed": False,
            "explanation": "Test response double; no detector or LLM executed.",
            "applied_policy_config": copy.deepcopy(policy), "implementation": "test_response_double"}


class FixtureTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cases = self.root / "dataset"
        self.labels = self.root / "private_labels.json"
        models = patch("data.generate_data._team_model", return_value=None)
        models.start()
        self.addCleanup(models.stop)
        export_dataset(self.cases, labels_path=self.labels)
        self.dataset = load_dataset(self.cases, self.labels)


class DataTests(FixtureTest):
    def test_exactly_twenty_individual_files_and_all_five_families(self):
        self.assertEqual(len(list(self.cases.glob("*.json"))), 20)
        self.assertEqual(len(self.dataset), 20)
        self.assertEqual({case["truth"]["label"] for case in self.dataset}, set(LABELS))
        self.assertEqual(sum(case["truth"]["invalid_proof"] for case in self.dataset), 14)
        self.assertTrue(all(not has_private_labels(case["input"]) for case in self.dataset))

    def test_depot_delegation_replay_and_three_injection_cases_exist(self):
        categories = {case["truth"]["category"] for case in self.dataset}
        self.assertTrue({"depot", "approved_delegate", "replay", "fraud_gps_spoof", "fraud_social_engineered_otp", "fraud_ghost_delivery"}.issubset(categories))
        self.assertEqual(sum(case["truth"]["category"] == "prompt_injection" for case in self.dataset), 3)

    def test_required_generation_functions_do_not_return_labels(self):
        with patch("data.generate_data._team_model", return_value=None):
            for label in LABELS:
                self.assertFalse(has_private_labels(generate_shipment(label)))
            self.assertEqual(generate_courier_history("SYN-H01")["courier_id"], "SYN-H01")

    def test_unknown_label_or_invalid_case_index_is_rejected(self):
        for kwargs in ({"label": "fake"}, {"label": "normal", "case_index": -1}, {"label": "normal", "case_index": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                generate_shipment(**kwargs)

    def test_labels_cannot_be_exported_inside_the_input_directory(self):
        with self.assertRaises(ValueError):
            export_dataset(self.cases, labels_path=self.cases / "labels.json")

    def test_mapper_cannot_leak_a_private_answer(self):
        with self.assertRaises(ValueError):
            generate_shipment("normal", shipment_mapper=lambda item: {**item, "expected_action": "ACCEPT"})

    def test_mapper_nan_is_rejected(self):
        with self.assertRaises(ValueError):
            generate_shipment("normal", shipment_mapper=lambda item: {**item, "amount": float("nan")})

    def test_requested_model_validation_is_called(self):
        class Model:
            seen = []
            @classmethod
            def model_validate(cls, payload):
                cls.seen.append(copy.deepcopy(payload))
                return cls()
        generate_shipment("normal", shipment_model=Model)
        self.assertEqual(len(Model.seen), 1)
        self.assertIn("parcel_id", Model.seen[0])

    def test_rejected_schema_writes_no_case_or_label_files(self):
        class BadModel:
            @classmethod
            def model_validate(cls, payload):
                raise ValueError("Test schema refuses this draft event.")
        cases, labels = self.root / "new_inputs", self.root / "new_labels.json"
        with self.assertRaises(ValueError):
            export_dataset(cases, labels_path=labels, shipment_model=BadModel)
        self.assertFalse(cases.exists())
        self.assertFalse(labels.exists())

    def test_explicit_draft_generates_without_importing_placeholder_backend(self):
        with patch("data.generate_data._team_model", side_effect=AssertionError("Backend must not be imported")) as lookup:
            shipment = generate_shipment("normal", draft=True)
            history = generate_courier_history("SYN-H01", draft=True)
            result = export_dataset(self.root / "draft", labels_path=self.root / "draft_labels.json", draft=True)
        lookup.assert_not_called()
        self.assertEqual(shipment["handoff_type"], "doorstep")
        self.assertEqual(history["courier_id"], "SYN-H01")
        self.assertEqual(result["case_count"], 20)
        for key in ("shipment_event", "courier_history", "trusted_shipment", "transfer_code"):
            self.assertEqual(result["schema_validation"][key], "not_checked")
        self.assertEqual(result["schema_validation"]["validation_mode"], "explicit_draft")

    def test_draft_cannot_claim_required_or_explicit_model_validation(self):
        class Model:
            pass
        for fn in (
            lambda: generate_shipment("normal", draft=True, require_team_schema=True),
            lambda: generate_shipment("normal", draft=True, shipment_model=Model),
            lambda: generate_courier_history("SYN-H01", draft=True, history_model=Model),
            lambda: export_dataset(self.root / "conflict", draft=True, require_team_schema=True),
            lambda: generate_shipment("normal", draft="false"),
        ):
            with self.assertRaises(ValueError):
                fn()

    def test_draft_generation_cli_runs_when_backend_lookup_would_fail(self):
        args = ["--draft", "--out", str(self.root / "cli_draft"), "--labels", str(self.root / "cli_draft_labels.json")]
        with patch("data.generate_data._team_model", side_effect=AttributeError("Placeholder has no models")):
            self.assertEqual(generate_main(args), 0)
        self.assertEqual(len(list((self.root / "cli_draft").glob("C*.json"))), 20)

    def test_case_id_mismatch_is_rejected(self):
        file = self.cases / "C01.json"
        data = json.loads(file.read_text())
        data["case_id"] = "C02"
        file.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            load_dataset(self.cases, self.labels)


class HarnessTests(FixtureTest):
    def test_baseline_observed_detection_and_misses_are_retained(self):
        rows = run_baseline(self.dataset)
        self.assertEqual(len(rows), 20)
        self.assertTrue(all(row["status"] == "ok" for row in rows))
        metrics = compute_metrics(rows)
        self.assertEqual(metrics["flagged_invalid_proofs"], 1)
        self.assertEqual(metrics["missed_invalid_proofs"], 13)
        self.assertAlmostEqual(compute_detection_rate(rows), 7.14)
        self.assertEqual(compute_false_alert_rate(rows), 0.0)
        self.assertEqual(metrics["unsupported_acceptances"], 19)
        self.assertGreater(compute_time_to_recommendation(rows), 0)
        self.assertIn("C20", metrics["missed_case_ids"])

    def test_full_modes_without_pipeline_are_unmeasured(self):
        for function in (run_rules_only, run_rules_and_llm):
            rows = function(self.dataset)
            self.assertTrue(all(row["status"] == "awaiting_pipeline" and row["observed_action"] is None for row in rows))
            metrics = compute_metrics(rows)
            self.assertEqual(metrics["evaluated_cases"], 0)
            self.assertIsNone(metrics["invalid_proof_detection_percent"])
            self.assertIsNone(metrics["false_alert_percent"])
            self.assertIsNone(metrics["mean_time_to_recommendation_ms"])

    def test_adapter_receives_no_private_labels_or_expected_answers(self):
        calls = []
        original = copy.deepcopy(self.dataset)
        def pipeline(*, fixture, mode, policy_config, evaluation_only):
            self.assertFalse(has_private_labels(fixture))
            self.assertTrue(evaluation_only)
            self.assertEqual(mode, "rules_only")
            calls.append(fixture["case_id"])
            fixture["shipment_event"]["notes"] = "Edited only the adapter's copy."
            return _response(policy_config)
        rows = run_rules_only(self.dataset, pipeline=pipeline)
        self.assertEqual(len(calls), 20)
        self.assertEqual(self.dataset, original)
        self.assertTrue(all(row["status"] == "ok" for row in rows))

    def test_adapter_exception_is_not_credited_as_detection(self):
        def unavailable(**kwargs):
            raise ConnectionError("Test pipeline unavailable.")
        metrics = compute_metrics(run_rules_only(self.dataset, pipeline=unavailable))
        self.assertEqual(metrics["adapter_errors"], 20)
        self.assertIsNone(metrics["invalid_proof_detection_percent"])

    def test_agent_usage_in_rules_only_is_reported_as_a_violation(self):
        def pipeline(**kwargs):
            result = _response(kwargs["policy_config"])
            result.update(llm_used=True, agent_invoked=True)
            return result
        rows = run_rules_only(self.dataset[:1], pipeline=pipeline)
        self.assertIn("rules_only_arm_used_an_investigator_or_llm", rows[0]["protocol_violations"])

    def test_report_preserves_unsafe_acceptance(self):
        def pipeline(**kwargs):
            return _response(kwargs["policy_config"], checks={"parcel_code": "PASS", "identity": "FAIL", "location": "UNKNOWN"})
        rows = run_rules_and_llm(self.dataset[:1], pipeline=pipeline)
        self.assertEqual(rows[0]["observed_action"], "ACCEPT")
        self.assertIn("acceptance_without_all_mandatory_checks_passing", rows[0]["protocol_violations"])
        self.assertEqual(compute_metrics(rows)["unsupported_acceptances"], 1)

    def test_executed_action_and_missing_approval_are_reported(self):
        def pipeline(**kwargs):
            result = _response(kwargs["policy_config"], action="HOLD")
            result.update(executed=True, requires_human_approval=False)
            return result
        row = run_rules_and_llm(self.dataset[:1], pipeline=pipeline)[0]
        self.assertIn("evaluation_executed_an_action", row["protocol_violations"])
        self.assertIn("disruptive_recommendation_missing_approval_requirement", row["protocol_violations"])

    def test_unsupported_policy_config_is_not_silently_accepted(self):
        def pipeline(**kwargs):
            result = _response(kwargs["policy_config"])
            result["applied_policy_config"] = POLICY_VARIANTS["strict"]
            return result
        row = run_rules_only(self.dataset[:1], pipeline=pipeline, policy="balanced")[0]
        self.assertEqual(row["status"], "adapter_error")

    def test_all_three_variants_reach_the_pipeline_unchanged(self):
        received = []
        def pipeline(**kwargs):
            received.append(copy.deepcopy(kwargs["policy_config"]))
            return _response(kwargs["policy_config"])
        for name, config in POLICY_VARIANTS.items():
            rows = run_variant(self.dataset[:1], name, pipeline=pipeline)
            self.assertEqual(rows[0]["profile"], name)
            self.assertEqual(received[-1], config)

    def test_policy_variants_cannot_change_mandatory_check_thresholds(self):
        invalid = {**POLICY_VARIANTS["balanced"], "skip_identity": True}
        with self.assertRaises(ValueError):
            policy_config(invalid)

    def test_non_allowlisted_tool_or_excessive_calls_are_reported(self):
        def pipeline(**kwargs):
            result = _response(kwargs["policy_config"])
            result["tool_calls"] = [{"name": "approve_delivery", "arguments": {}, "status": "OK"} for _ in range(4)]
            return result
        row = run_rules_and_llm(self.dataset[:1], pipeline=pipeline)[0]
        self.assertIn("reported_tool_budget_exceeded", row["protocol_violations"])
        self.assertIn("non_allowlisted_tool_reported_as_successful", row["protocol_violations"])

    def test_reports_mark_full_comparison_pending_and_escape_html(self):
        rows = run_baseline(self.dataset) + run_rules_only(self.dataset) + run_rules_and_llm(self.dataset)
        rows[0]["explanation"] = '<script>alert("test")</script>'
        report = write_results(rows, self.root / "results", dataset=self.dataset)
        self.assertEqual(report["unique_case_count"], 20)
        self.assertEqual(report["configuration_row_count"], 60)
        html = (self.root / "results/report.html").read_text()
        self.assertNotIn('<script>alert("test")</script>', html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("No LLM usage", html)
        self.assertIn("not measured", (self.root / "results/observed_results.md").read_text())

    def test_cli_draft_is_successful_and_missing_pipeline_is_nonzero(self):
        args = ["--cases", str(self.cases), "--labels", str(self.labels), "--out", str(self.root / "cli_results"), "--profiles", "balanced"]
        self.assertEqual(main(args + ["--draft"]), 0)
        self.assertEqual(main(args), 2)


class MetricTests(unittest.TestCase):
    def test_denominators_latency_and_unresolved_are_independent(self):
        def row(case_id, invalid, action, latency):
            return {"case_id": case_id, "invalid_proof": invalid, "valid_handoff": not invalid,
                    "status": "ok", "observed_action": action, "latency_ms": latency,
                    "checks": {"parcel_code": "PASS", "identity": "PASS", "location": "PASS"},
                    "tool_calls": [], "llm_used": False, "agent_invoked": False, "protocol_violations": []}
        rows = [row("C01", True, "HOLD", 10), row("C02", True, "ACCEPT", 20),
                row("C03", False, "VERIFY", 30), row("C04", False, "ACCEPT", 40)]
        rows.append({"status": "adapter_error"})
        self.assertEqual(compute_detection_rate(rows), 50.0)
        self.assertEqual(compute_false_alert_rate(rows), 50.0)
        self.assertEqual(compute_time_to_recommendation(rows), 25.0)
        metrics = compute_metrics(rows)
        self.assertEqual(metrics["coverage_percent"], 80.0)
        self.assertEqual(metrics["unresolved_cases"], 1)

    def test_empty_denominators_return_none(self):
        self.assertIsNone(compute_detection_rate([]))
        self.assertIsNone(compute_false_alert_rate([]))
        self.assertIsNone(compute_time_to_recommendation([]))


if __name__ == "__main__":
    unittest.main()
