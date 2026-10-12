"""Raja: proof-quality metrics. Rates are percentages; latency is milliseconds."""

from statistics import mean


def _successful(rows):
    return [row for row in rows if row["status"] == "ok"]


def _rate(numerator, denominator):
    return round(100 * numerator / denominator, 2) if denominator else None


def compute_detection_rate(rows):
    """Flagged invalid proofs / evaluated invalid proofs, not confirmed fraud."""
    cases = [row for row in _successful(rows) if row["invalid_proof"]]
    return _rate(sum(row["observed_action"] != "ACCEPT" for row in cases), len(cases))


def compute_false_alert_rate(rows):
    cases = [row for row in _successful(rows) if not row["invalid_proof"]]
    return _rate(sum(row["observed_action"] != "ACCEPT" for row in cases), len(cases))


def compute_time_to_recommendation(rows):
    measured = [row["latency_ms"] for row in _successful(rows)]
    return round(mean(measured), 4) if measured else None


def compute_metrics(rows):
    ok = _successful(rows)
    invalid = [row for row in ok if row["invalid_proof"]]
    valid = [row for row in ok if not row["invalid_proof"]]
    missed = [row["case_id"] for row in invalid if row["observed_action"] == "ACCEPT"]
    flagged_invalid = sum(row["observed_action"] != "ACCEPT" for row in invalid)
    false_alerts = sum(row["observed_action"] != "ACCEPT" for row in valid)
    legitimate = [row for row in ok if row["valid_handoff"]]
    return {"total_cases": len(rows), "evaluated_cases": len(ok),
            "coverage_percent": _rate(len(ok), len(rows)),
            "awaiting_pipeline": sum(row["status"] == "awaiting_pipeline" for row in rows),
            "adapter_errors": sum(row["status"] == "adapter_error" for row in rows),
            "invalid_proof_cases_evaluated": len(invalid), "valid_proof_cases_evaluated": len(valid),
            "flagged_invalid_proofs": flagged_invalid, "missed_invalid_proofs": len(missed),
            "false_alerts": false_alerts, "invalid_proof_detection_percent": compute_detection_rate(rows),
            "false_alert_percent": compute_false_alert_rate(rows),
            "unsupported_acceptances": sum(row["observed_action"] == "ACCEPT" and
                                          any(value != "PASS" for value in row["checks"].values()) for row in ok),
            "unresolved_cases": sum(row["observed_action"] in ("VERIFY", "REVIEW") for row in ok),
            "tool_calls": sum(len(row["tool_calls"]) for row in ok),
            "mean_time_to_recommendation_ms": compute_time_to_recommendation(rows),
            "legitimate_handoff_review_burden_percent": _rate(sum(row["observed_action"] != "ACCEPT" for row in legitimate), len(legitimate)),
            "llm_cases_service_reported": sum(row["llm_used"] for row in ok),
            "agent_invoked_cases": sum(row["agent_invoked"] for row in ok),
            "protocol_violation_cases": sum(bool(row["protocol_violations"]) for row in ok),
            "missed_case_ids": missed}
