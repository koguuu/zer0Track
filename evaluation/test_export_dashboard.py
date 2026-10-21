"""Check honest comparisons, summary validation and exclusion of raw fields."""

import copy
import json
from pathlib import Path
import tempfile
import unittest

from evaluation.export_dashboard import MODES, PROFILES, build_export, main
from evaluation.metrics import compute_metrics


def sample_report():
    rows, summaries = [], []
    for profile in PROFILES:
        for mode in MODES:
            group = []
            for case_id, invalid in (("C01", False), ("C02", True)):
                agent = mode == "rules_and_llm" and invalid
                group.append({
                    "case_id": case_id, "profile": profile, "mode": mode,
                    "title": "Synthetic scenario " + case_id,
                    "status": "ok", "invalid_proof": invalid, "valid_handoff": not invalid,
                    "observed_action": "VERIFY" if invalid else "ACCEPT",
                    "checks": {"parcel_code": "PASS", "identity": "PASS",
                               "location": "UNKNOWN" if invalid else "PASS"},
                    "latency_ms": 2.0 if agent else 1.0,
                    "agent_invoked": agent, "llm_used": False, "protocol_violations": [],
                    "tool_calls": [{"name": "lookup_checkpoint", "status": "OK",
                                    "arguments": {"notes": "RAW_SECRET_123456"}}] if agent else [],
                    "notes": "RAW_SECRET_123456", "expected_action": "PRIVATE_EXPECTED",
                    "explanation": "RAW_SECRET_123456", "error": "RAW_SECRET_123456",
                    "label": "PRIVATE_LABEL", "backend_decision": {"reasoning": "RAW_SECRET_123456"},
                })
            summaries.append({"profile": profile, "mode": mode, **compute_metrics(group)})
            rows.extend(group)
    return {"generated_at": "2026-10-13T19:02:07+00:00", "source": "synthetic",
            "unique_case_count": 2, "configuration_row_count": len(rows),
            "dataset_sha256": None, "llm_usage_is_service_reported": True,
            "operator_review_time_measured": False, "rows": rows, "summary": summaries}


def refresh_summaries(report):
    for summary in report["summary"]:
        group = [row for row in report["rows"] if
                 (row["profile"], row["mode"]) == (summary["profile"], summary["mode"])]
        summary.update(compute_metrics(group))


class DashboardExportTests(unittest.TestCase):
    def test_paired_metrics_and_agent_use_without_claiming_llm_use(self):
        result = build_export(sample_report())
        self.assertEqual(result["metadata"]["exported_configuration_row_count"], 12)
        profile = result["profiles"]["balanced"]
        self.assertEqual(profile["comparison"]["changed_decision_count"], 0)
        agent = profile["summary"]["rules_and_llm"]
        self.assertEqual(agent["flagged_invalid_proofs"], 1)
        self.assertEqual(agent["invalid_proof_cases_evaluated"], 1)
        self.assertEqual(agent["agent_invoked_cases"], 1)
        self.assertEqual(agent["llm_cases_service_reported"], 0)

    def test_private_fields_and_nested_raw_arguments_are_excluded(self):
        text = json.dumps(build_export(sample_report()))
        for token in ("RAW_SECRET", "PRIVATE_EXPECTED", "PRIVATE_LABEL", '"arguments"',
                      '"expected_action"', '"explanation"', '"invalid_proof"'):
            self.assertNotIn(token, text)

    def test_input_is_unchanged_and_source_timestamp_is_preserved(self):
        report = sample_report()
        before = copy.deepcopy(report)
        result = build_export(report, exported_at="2026-10-22T00:00:00+00:00")
        self.assertEqual(report, before)
        self.assertEqual(result["metadata"]["evaluation_generated_at"], report["generated_at"])

    def test_duplicate_and_missing_pair_are_rejected(self):
        report = sample_report()
        report["rows"].append(copy.deepcopy(report["rows"][0]))
        report["configuration_row_count"] += 1
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            build_export(report)
        report = sample_report()
        report["rows"].pop()
        report["configuration_row_count"] -= 1
        with self.assertRaisesRegex(ValueError, "case set"):
            build_export(report)

    def test_incorrect_summary_is_rejected(self):
        report = sample_report()
        report["summary"][0]["invalid_proof_detection_percent"] = 99
        with self.assertRaisesRegex(ValueError, "Summary mismatch"):
            build_export(report)

    def test_failed_or_pending_rows_never_count_as_unchanged_successes(self):
        report = sample_report()
        for row in report["rows"]:
            if row["profile"] == "balanced" and row["mode"] == "rules_and_llm":
                row["status"] = "adapter_error" if row["case_id"] == "C01" else "awaiting_pipeline"
                row["observed_action"] = None
                row["latency_ms"] = None
        refresh_summaries(report)
        profile = build_export(report)["profiles"]["balanced"]
        self.assertEqual(profile["comparison"]["paired_successful_case_count"], 0)
        self.assertEqual(profile["comparison"]["uncompared_case_count"], 2)
        self.assertIsNone(profile["comparison"]["changed_decision_count"])
        self.assertIsNone(profile["summary"]["rules_and_llm"]["invalid_proof_detection_percent"])
        self.assertIsNone(profile["summary"]["rules_and_llm"]["mean_time_to_recommendation_ms"])
        self.assertTrue(all(case["decision_changed"] is None for case in profile["cases"]))
        self.assertIsNone(profile["cases"][0]["modes"]["rules_and_llm"]["tool_call_count"])

    def test_actual_changed_action_is_counted(self):
        report = sample_report()
        row = next(row for row in report["rows"] if row["profile"] == "balanced"
                   and row["mode"] == "rules_and_llm" and row["case_id"] == "C02")
        row["observed_action"] = "ESCALATE"
        refresh_summaries(report)
        profile = build_export(report)["profiles"]["balanced"]
        self.assertEqual(profile["comparison"]["changed_decision_count"], 1)

    def test_non_finite_latency_is_rejected(self):
        report = sample_report()
        report["rows"][0]["latency_ms"] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            build_export(report)

    def test_cli_records_source_hash_and_prevents_overwrite(self):
        import hashlib
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / "results.json", Path(temp) / "dashboard.json"
            source.write_text(json.dumps(sample_report()), encoding="utf-8")
            before = source.read_bytes()
            self.assertEqual(main(["--input", str(source), "--output", str(output)]), 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["metadata"]["source_sha256"], hashlib.sha256(before).hexdigest())
            with self.assertRaises(SystemExit):
                main(["--input", str(source), "--output", str(source)])
            self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
