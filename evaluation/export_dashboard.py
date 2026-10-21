"""Export stored evaluation results for Nour's comparison view; no live calls."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path

from evaluation.metrics import compute_metrics


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ("strict", "balanced", "loose")
MODES = ("rules_only", "rules_and_llm")
CHECKS = ("parcel_code", "identity", "location")
ACTIONS = {"ACCEPT", "VERIFY", "REVIEW", "HOLD", "ESCALATE", "REJECT"}
STATUSES = {"ok", "awaiting_pipeline", "adapter_error"}
SUMMARY_FIELDS = (
    "total_cases", "evaluated_cases", "coverage_percent", "awaiting_pipeline",
    "adapter_errors", "invalid_proof_cases_evaluated", "valid_proof_cases_evaluated",
    "flagged_invalid_proofs", "missed_invalid_proofs", "false_alerts",
    "invalid_proof_detection_percent", "false_alert_percent",
    "unsupported_acceptances", "unresolved_cases", "tool_calls",
    "mean_time_to_recommendation_ms", "legitimate_handoff_review_burden_percent",
    "llm_cases_service_reported", "agent_invoked_cases", "protocol_violation_cases",
)
LIMITATIONS = [
    "Stored synthetic evaluation; toggling modes does not investigate a live case.",
    "Cases repeat across configurations; configuration rows are not independent samples.",
    "Detection means invalid proof flagged for review, not confirmed fraud.",
    "Rates exclude pending and failed rows; use the exported denominators.",
    "Agent invocation and tool calls do not by themselves establish LLM use or benefit.",
    "LLM usage is service-reported, not independently verified provider billing.",
    "Recommendation latency is evaluation timing, not API latency or operator review time.",
    "Investigation profiles do not change the mandatory rules-only checks.",
    "Equal recommendations do not establish improved detection or reduced human effort.",
]


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _validate_row(row):
    _require(isinstance(row, dict), "Each selected result must be an object.")
    _require(isinstance(row.get("case_id"), str) and bool(row["case_id"]),
             "Each selected result needs a case ID.")
    _require(isinstance(row.get("title"), str), "Each selected result needs a title.")
    _require(row.get("status") in STATUSES, "Unknown result status.")
    for field in ("invalid_proof", "valid_handoff", "agent_invoked", "llm_used"):
        _require(type(row.get(field)) is bool, "Missing boolean field: " + field)
    _require(isinstance(row.get("checks"), dict) and set(row["checks"]) == set(CHECKS),
             "Expected exactly the three evaluation check names.")
    _require(all(value in {"PASS", "FAIL", "UNKNOWN"} for value in row["checks"].values()),
             "Unknown mandatory check status.")
    _require(isinstance(row.get("tool_calls"), list), "Tool calls must be a list.")
    for call in row["tool_calls"]:
        _require(isinstance(call, dict) and isinstance(call.get("name"), str)
                 and call.get("status") in {"OK", "ERROR", "REJECTED"},
                 "Tool calls need a name and an OK/ERROR/REJECTED status.")
    _require(isinstance(row.get("protocol_violations"), list),
             "Protocol violations must be a list.")
    if row["status"] == "ok":
        _require(row.get("observed_action") in ACTIONS, "Successful result lacks a valid action.")
        latency = row.get("latency_ms")
        _require(type(latency) in (int, float) and math.isfinite(latency) and latency >= 0,
                 "Successful result needs finite, non-negative latency_ms.")


def _same_metric(left, right):
    if left is None or right is None:
        return left is right
    if type(left) not in (int, float) or type(right) not in (int, float):
        return False
    return math.isfinite(left) and math.isclose(left, right, rel_tol=0, abs_tol=1e-8)


def _mode_result(row):
    """Explicit projection: notes, labels, arguments and errors cannot leak."""
    successful = row["status"] == "ok"
    return {
        "status": row["status"],
        "observed_action": row["observed_action"] if successful else None,
        "checks": {name: row["checks"][name] for name in CHECKS} if successful else None,
        "latency_ms": row["latency_ms"] if successful else None,
        "agent_invoked": row["agent_invoked"] if successful else None,
        "llm_used_service_reported": row["llm_used"] if successful else None,
        "tool_call_count": len(row["tool_calls"]) if successful else None,
        "tools": [{"name": call["name"], "status": call["status"]}
                  for call in row["tool_calls"]] if successful else [],
        "protocol_violation_count": len(row["protocol_violations"]) if successful else None,
    }


def build_export(report, *, source_sha256=None, exported_at=None):
    """Validate matching case sets and summaries, then return a compact projection."""
    _require(isinstance(report, dict) and report.get("source") == "synthetic",
             "Expected a synthetic evaluation report.")
    _require(isinstance(report.get("rows"), list) and isinstance(report.get("summary"), list),
             "Report requires rows and summary arrays.")
    _require(isinstance(report.get("generated_at"), str), "Missing evaluation timestamp.")
    _require(report.get("llm_usage_is_service_reported") is True,
             "Expected explicit service-reported LLM usage metadata.")
    _require(type(report.get("operator_review_time_measured")) is bool,
             "Missing operator review time measurement flag.")
    groups = {(profile, mode): {} for profile in PROFILES for mode in MODES}
    all_keys = set()
    for row in report["rows"]:
        _require(isinstance(row, dict), "Each result must be an object.")
        key = (row.get("profile"), row.get("mode"), row.get("case_id"))
        _require(all(isinstance(value, str) for value in key),
                 "Each result needs profile, mode and case_id strings.")
        _require(key not in all_keys, "Duplicate profile/mode/case: " + "/".join(key))
        all_keys.add(key)
        group = key[:2]
        if group not in groups:
            _require(key[0] in PROFILES and key[1] == "scan_only", "Unknown profile or mode.")
            continue
        _validate_row(row)
        groups[group][row["case_id"]] = row
    _require(report.get("configuration_row_count") == len(report["rows"]),
             "Source configuration row count does not match rows.")
    case_ids = set(groups[(PROFILES[0], MODES[0])])
    _require(bool(case_ids), "No comparison cases found.")
    for key, rows in groups.items():
        _require(set(rows) == case_ids, "Missing or mismatched case set: " + "/".join(key))
    _require(report.get("unique_case_count") == len(case_ids), "Unique case count mismatch.")
    for case_id in case_ids:
        variants = [rows[case_id] for rows in groups.values()]
        signatures = {(row["title"], row["invalid_proof"], row["valid_handoff"]) for row in variants}
        _require(len(signatures) == 1, "Scenario metadata differs for " + case_id)
    summaries = {}
    for summary in report["summary"]:
        _require(isinstance(summary, dict), "Each summary must be an object.")
        key = (summary.get("profile"), summary.get("mode"))
        if key not in groups:
            continue
        _require(key not in summaries, "Duplicate summary: " + "/".join(key))
        computed = compute_metrics(list(groups[key].values()))
        for field in SUMMARY_FIELDS:
            _require(field in summary and _same_metric(summary[field], computed[field]),
                     "Summary mismatch: " + "/".join(key) + "/" + field)
        summaries[key] = {field: summary[field] for field in SUMMARY_FIELDS}
    _require(set(summaries) == set(groups), "Missing a required comparison summary.")
    profiles = {}
    for profile in PROFILES:
        cases = []
        for case_id in sorted(case_ids):
            rows = {mode: groups[(profile, mode)][case_id] for mode in MODES}
            comparable = all(row["status"] == "ok" for row in rows.values())
            cases.append({
                "case_id": case_id, "title": rows[MODES[0]]["title"],
                "modes": {mode: _mode_result(rows[mode]) for mode in MODES},
                "decision_changed": (rows[MODES[0]]["observed_action"] !=
                                     rows[MODES[1]]["observed_action"]) if comparable else None,
            })
        paired = sum(case["decision_changed"] is not None for case in cases)
        profiles[profile] = {
            "summary": {mode: summaries[(profile, mode)] for mode in MODES},
            "comparison": {
                "total_case_count": len(cases), "paired_successful_case_count": paired,
                "uncompared_case_count": len(cases) - paired,
                "changed_decision_count": sum(case["decision_changed"] is True for case in cases)
                                          if paired else None,
            },
            "cases": cases,
        }
    return {
        "schema_version": 1,
        "metadata": {
            "evaluation_generated_at": report["generated_at"],
            "exported_at": exported_at or datetime.now(timezone.utc).isoformat(),
            "source": report["source"], "source_sha256": source_sha256,
            "dataset_sha256": report.get("dataset_sha256"),
            "unique_case_count": len(case_ids),
            "source_configuration_row_count": len(report["rows"]),
            "exported_configuration_row_count": sum(len(rows) for rows in groups.values()),
            "llm_usage_is_service_reported": report["llm_usage_is_service_reported"],
            "operator_review_time_measured": report["operator_review_time_measured"],
        },
        "default_profile": "balanced",
        "mode_labels": {"rules_only": "Rules only", "rules_and_llm": "Agent-assisted"},
        "profiles": profiles,
        "limitations": LIMITATIONS.copy(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "evaluation/results/results.json")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "evaluation/results/dashboard_comparison.json")
    parser.add_argument("--pretty", action="store_true", help="Indent JSON for manual inspection.")
    args = parser.parse_args(argv)
    try:
        _require(args.input.resolve() != args.output.resolve(), "Output must not overwrite input.")
        raw = args.input.read_bytes()
        payload = build_export(json.loads(raw), source_sha256=hashlib.sha256(raw).hexdigest())
        content = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                             indent=2 if args.pretty else None,
                             separators=None if args.pretty else (",", ":")) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(f"Exported {payload['metadata']['unique_case_count']} cases across 3 profiles: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
