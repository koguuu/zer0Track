"""Raja: comparison harness, private scoring and observed-results reporting.

The scan-only comparator is implemented here. The Bhone bridge imports the real
backend checkers/policy; a custom team pipeline can supply the real agent arm.
"""

import argparse
import copy
import csv
from datetime import datetime, timezone
import hashlib
import html
import importlib
import json
from pathlib import Path
import re
import time

from data.generate_data import ROOT, LABELS, has_private_labels
from evaluation.metrics import compute_metrics
from evaluation.policy_variants import POLICY_VARIANTS, policy_config

MODES = ("scan_only", "rules_only", "rules_and_llm")
ACTIONS = ("ACCEPT", "VERIFY", "HOLD", "ESCALATE", "REVIEW", "REJECT")
CHECKS = ("parcel_code", "identity", "location")
TOOLS = ("lookup_delegation", "lookup_checkpoint", "lookup_custody_history")


class PipelineUnavailable(RuntimeError):
    """A missing comparison arm stays pending rather than scoring a fake result."""


def evaluate_with_bhone(*, fixture, mode, policy_config, evaluation_only):
    """Raja's read-only bridge to the supplied backend; no checker is duplicated.

    Calls pure backend functions with an explicit synthetic clock, never Store,
    /approve or /evaluate. This measures checker/policy execution, not API latency.
    Investigation configurations are inactive in the mandatory-rules-only arm.
    """
    if evaluation_only is not True:
        raise ValueError("This bridge is restricted to evaluation_only=True.")
    if mode == "rules_and_llm":
        raise PipelineUnavailable("Bhone is connected; Kogu's real investigator/tool gateway is not connected yet.")
    if mode != "rules_only":
        raise ValueError("Bhone's bridge supports only the mandatory-rules-only arm.")
    if fixture.get("fixture_format") != "raja-draft-v2" or fixture.get("source") != "synthetic" or has_private_labels(fixture):
        raise ValueError("Regenerate Raja's public v2 synthetic fixtures before connecting this backend.")
    from backend.checkers import run_mandatory_checks
    from backend.models import ShipmentEvent, CourierHistory, TrustedShipmentRecord, TransferCodeRecord
    from backend.policy import evaluate_policy
    event = ShipmentEvent.model_validate(fixture["shipment_event"])
    CourierHistory.model_validate(fixture["courier_history"])
    context = fixture["fixture_context"]
    trusted = TrustedShipmentRecord.model_validate(context["trusted_shipment"])
    code = TransferCodeRecord.model_validate(context["transfer_code"]) if context["transfer_code"] is not None else None
    now = datetime.fromisoformat(context["reference_time"].replace("Z", "+00:00"))
    if now.tzinfo is None:
        raise ValueError("Synthetic reference clock must include a timezone.")
    handler = context["authenticated_handler_id"]
    if not isinstance(handler, str) or not handler:
        raise ValueError("Provide an independently seeded synthetic authenticated handler.")
    checks = run_mandatory_checks(event, trusted, code, handler, now=now)
    decision = evaluate_policy(checks)
    names = {"parcel_code_integrity": "parcel_code", "identity_authority": "identity", "location": "location"}
    actions = {"ACCEPT_READY": "ACCEPT", "REQUEST_VERIFICATION": "VERIFY", "RECOMMEND_HOLD": "HOLD", "ESCALATE": "ESCALATE"}
    return {"action": actions[decision.decision],
            "checks": {names[check.checker_name]: check.status.value for check in checks},
            "tool_calls": [], "executed": False, "requires_human_approval": decision.requires_human_approval,
            "llm_used": False, "agent_invoked": False, "notes_exposed": False,
            "explanation": decision.reason + " (Recommendation only; no custody update or code consumption.)",
            "applied_policy_config": copy.deepcopy(policy_config), "investigation_settings_active": False,
            "backend_decision": decision.model_dump(mode="json"),
            "backend_checks": [check.model_dump(mode="json") for check in checks],
            "implementation": "supplied_bhone_pure_checks_and_policy"}


def load_dataset(case_dir=None, labels_path=None):
    folder = Path(case_dir) if case_dir is not None else ROOT / "data" / "dataset"
    private_path = Path(labels_path) if labels_path is not None else ROOT / "evaluation" / "private_labels.json"
    private = json.loads(private_path.read_text(encoding="utf-8"))
    labels = {}
    for truth in private["records"]:
        if truth["case_id"] in labels:
            raise ValueError("Duplicate private case ID.")
        labels[truth["case_id"]] = truth
    paths = sorted(folder.glob("C[0-9][0-9].json"))
    if not paths:
        raise ValueError("No case files found. Run python -m data.generate_data first.")
    records = []
    for path in paths:
        fixture = json.loads(path.read_text(encoding="utf-8"))
        case_id = fixture.get("case_id")
        if case_id != path.stem or case_id not in labels:
            raise ValueError("Case filename, payload ID and private label ID must agree.")
        records.append({"input": fixture, "truth": copy.deepcopy(labels[case_id])})
    return _dataset(records)


def _dataset(dataset):
    if dataset is None or isinstance(dataset, (str, Path)):
        return load_dataset(dataset)
    if not isinstance(dataset, list) or not dataset:
        raise ValueError("Use a case directory or the list returned by load_dataset().")
    result, seen = copy.deepcopy(dataset), set()
    for record in result:
        fixture, truth = record["input"], record["truth"]
        if not isinstance(fixture, dict) or has_private_labels(fixture):
            raise ValueError("Public fixture must not contain answer labels.")
        case_id = fixture.get("case_id")
        if not isinstance(case_id, str) or not re.fullmatch(r"C\d{2}", case_id) or case_id in seen or truth["case_id"] != case_id:
            raise ValueError("Case IDs must be unique and match their private labels.")
        seen.add(case_id)
        if fixture.get("fixture_format") != "raja-draft-v2" or fixture.get("source") != "synthetic":
            raise ValueError("Regenerate inputs: this draft accepts the v2 synthetic fixture format only.")
        for key in ("shipment_event", "courier_history", "fixture_context", "schema_validation"):
            if not isinstance(fixture.get(key), dict):
                raise ValueError("Missing public fixture field: " + key)
        if truth.get("label") not in LABELS or any(type(truth.get(key)) is not bool for key in ("invalid_proof", "valid_handoff")):
            raise ValueError("Private labels must use the agreed families and boolean truth fields.")
        if truth.get("expected_action") not in ACTIONS or any(action not in ACTIONS for action in truth.get("expected_by_profile", {}).values()):
            raise ValueError("Private expected actions must be recognized.")
        if any(key not in MODES or value not in ACTIONS for key, value in truth.get("expected_by_mode", {}).items()):
            raise ValueError("Private mode-specific expectations must use recognized modes/actions.")
    json.dumps(result, allow_nan=False)
    return result


def _scan_only(fixture, policy):
    scan = fixture["fixture_context"]["baseline_scan"]
    scanned = scan.get("scanned_parcel_id")
    passed = isinstance(scanned, str) and bool(scanned) and scanned == scan.get("manifest_parcel_id")
    return {"action": "ACCEPT" if passed else "ESCALATE", "checks": {key: "UNKNOWN" for key in CHECKS},
            "tool_calls": [], "requires_human_approval": not passed, "executed": False,
            "llm_used": False, "agent_invoked": False, "notes_exposed": False,
            "explanation": "Scan-only comparison. Code validity, authority and location are intentionally unverified.",
            "applied_policy_config": copy.deepcopy(policy), "implementation": "raja_scan_only_baseline"}


def _validate_response(response, policy):
    if not isinstance(response, dict):
        raise ValueError("Team adapter must return the normalized evaluation response dictionary.")
    json.dumps(response, allow_nan=False)
    if response.get("action") not in ACTIONS:
        raise ValueError("Adapter must map the team's recommendation to a recognized action.")
    checks = response.get("checks")
    if not isinstance(checks, dict) or set(checks) != set(CHECKS) or any(value not in ("PASS", "FAIL", "UNKNOWN") for value in checks.values()):
        raise ValueError("Provide all three normalized check statuses; missing evidence cannot be PASS.")
    for key in ("executed", "requires_human_approval", "llm_used", "agent_invoked", "notes_exposed"):
        if type(response.get(key)) is not bool:
            raise ValueError("Adapter must explicitly report boolean " + key)
    if not isinstance(response.get("explanation"), str) or not isinstance(response.get("tool_calls"), list):
        raise ValueError("Response needs explanation text and a tool-call list.")
    for call in response["tool_calls"]:
        if (not isinstance(call, dict) or not isinstance(call.get("name"), str)
                or not isinstance(call.get("arguments"), dict) or call.get("status") not in ("OK", "ERROR", "REJECTED")):
            raise ValueError("Tool traces need name, arguments and OK/ERROR/REJECTED status.")
    if response.get("applied_policy_config") != policy:
        raise ValueError("Team adapter must confirm the requested investigation config was applied.")
    return response


def _violations(response, mode, policy):
    found = []
    if response["executed"]:
        found.append("evaluation_executed_an_action")
    if response["action"] in ("HOLD", "ESCALATE", "REJECT") and not response["requires_human_approval"]:
        found.append("disruptive_recommendation_missing_approval_requirement")
    if mode != "scan_only" and response["action"] == "ACCEPT" and any(value != "PASS" for value in response["checks"].values()):
        found.append("acceptance_without_all_mandatory_checks_passing")
    if mode == "rules_only" and (response["llm_used"] or response["agent_invoked"]):
        found.append("rules_only_arm_used_an_investigator_or_llm")
    if response["llm_used"] and not response["agent_invoked"]:
        found.append("inconsistent_llm_usage_metadata")
    if len(response["tool_calls"]) > policy["max_tool_calls"]:
        found.append("reported_tool_budget_exceeded")
    if any(call["name"] not in TOOLS and call["status"] == "OK" for call in response["tool_calls"]):
        found.append("non_allowlisted_tool_reported_as_successful")
    return found


def run_configuration(dataset, mode, *, pipeline=None, policy="balanced"):
    if mode not in MODES:
        raise ValueError("Unknown comparison mode.")
    records = _dataset(dataset)
    config = policy_config(policy)
    if pipeline is not None and not callable(pipeline):
        raise ValueError("pipeline must be a callable team integration adapter.")
    rows = []
    for record in records:
        fixture, truth = record["input"], record["truth"]
        row = {"case_id": fixture["case_id"], "label": truth["label"], "category": truth["category"],
               "title": truth["title"], "invalid_proof": truth["invalid_proof"], "valid_handoff": truth["valid_handoff"],
               "expected_action": (truth.get("expected_by_profile", {}).get(config["name"], truth.get("expected_by_mode", {}).get(mode, truth["expected_action"]))
                                   if mode == "rules_and_llm" else truth.get("expected_by_mode", {}).get(mode, truth["expected_action"])),
               "mode": mode, "profile": config["name"], "policy_config": copy.deepcopy(config),
               "status": "ok", "observed_action": None, "checks": {key: "UNKNOWN" for key in CHECKS},
               "tool_calls": [], "llm_used": False, "agent_invoked": False, "notes_exposed": False,
               "executed": False, "requires_human_approval": False, "explanation": "", "error": None,
               "latency_ms": None, "protocol_violations": [], "implementation": "pending",
               "backend_decision": None, "backend_checks": [], "investigation_settings_active": None,
               "schema_validation": copy.deepcopy(fixture["schema_validation"])}
        if mode != "scan_only" and pipeline is None:
            row.update(status="awaiting_pipeline", error="Bhone/Kogu evaluation adapter is not connected.")
            rows.append(row)
            continue
        started = time.perf_counter()
        try:
            # No truth, labels, expected actions or title/category reach the adapter.
            public_fixture = copy.deepcopy(fixture)
            response = (_scan_only(public_fixture, config) if mode == "scan_only" else
                        pipeline(fixture=public_fixture, mode=mode, policy_config=copy.deepcopy(config), evaluation_only=True))
            response = _validate_response(response, config)
            row.update(observed_action=response["action"], checks=copy.deepcopy(response["checks"]),
                       tool_calls=copy.deepcopy(response["tool_calls"]), llm_used=response["llm_used"],
                       agent_invoked=response["agent_invoked"], notes_exposed=response["notes_exposed"],
                       executed=response["executed"], requires_human_approval=response["requires_human_approval"],
                       explanation=response["explanation"], implementation=response.get("implementation", "team_adapter"),
                       backend_decision=copy.deepcopy(response.get("backend_decision")),
                       backend_checks=copy.deepcopy(response.get("backend_checks", [])),
                       investigation_settings_active=response.get("investigation_settings_active"),
                       protocol_violations=_violations(response, mode, config))
        except PipelineUnavailable as exc:
            row.update(status="awaiting_pipeline", error=str(exc))
            rows.append(row)
            continue
        except Exception as exc:
            # No fallback recommendation is credited as a detection.
            row.update(status="adapter_error", error=f"{type(exc).__name__}: {exc}")
        row["latency_ms"] = round((time.perf_counter() - started) * 1000, 4)
        rows.append(row)
    return rows


def run_baseline(dataset=None, *, pipeline=None, policy="balanced"):
    """Assigned interface: local scan-only comparator, no backend writes."""
    return run_configuration(dataset, "scan_only", pipeline=pipeline, policy=policy)


def run_rules_only(dataset=None, *, pipeline=None, policy="balanced"):
    """Assigned interface: delegate fixed rules to the team's evaluation adapter."""
    return run_configuration(dataset, "rules_only", pipeline=pipeline, policy=policy)


def run_rules_and_llm(dataset=None, *, pipeline=None, policy="balanced"):
    """Assigned interface: delegate investigation, retaining actual usage metadata."""
    return run_configuration(dataset, "rules_and_llm", pipeline=pipeline, policy=policy)


def _csv(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            safe = {}
            for field in fields:
                value = row.get(field)
                if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                    value = "'" + value
                safe[field] = value
            writer.writerow(safe)


def write_results(rows, output_dir=None, *, dataset=None):
    if not rows:
        raise ValueError("No predictions to report.")
    folder = Path(output_dir) if output_dir is not None else ROOT / "evaluation" / "results"
    folder.mkdir(parents=True, exist_ok=True)
    groups = {}
    for row in rows:
        groups.setdefault((row["profile"], row["mode"]), []).append(row)
    summaries = [{"profile": profile, "mode": mode, **compute_metrics(group)} for (profile, mode), group in groups.items()]
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "source": "synthetic",
              "unique_case_count": len({row["case_id"] for row in rows}), "configuration_row_count": len(rows),
              "dataset_sha256": hashlib.sha256(json.dumps(dataset, sort_keys=True, allow_nan=False).encode()).hexdigest() if dataset is not None else None,
              "llm_usage_is_service_reported": True, "operator_review_time_measured": False,
              "summary": summaries, "rows": rows}
    (folder / "results.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")
    _csv(folder / "metrics.csv", summaries, [key for key in summaries[0] if key != "missed_case_ids"])
    _csv(folder / "predictions.csv", rows, ["case_id", "label", "category", "profile", "mode", "status", "expected_action", "observed_action", "invalid_proof", "latency_ms", "llm_used", "agent_invoked", "error"])
    def text(value):
        return "N/A" if value is None else str(value)
    headers = ("Profile", "Mode", "Evaluated", "Detection %", "False alerts %", "Unsupported accepts", "Unresolved", "Tools", "Mean ms")
    keys = ("profile", "mode", "evaluated_cases", "invalid_proof_detection_percent", "false_alert_percent", "unsupported_acceptances", "unresolved_cases", "tool_calls", "mean_time_to_recommendation_ms")
    pending = sum(row["status"] == "awaiting_pipeline" for row in rows)
    errors = sum(row["status"] == "adapter_error" for row in rows)
    warning = f"Synthetic evaluation. Pending pipeline rows: {pending}; adapter errors: {errors}."
    llm = sum(row["llm_used"] for row in rows if row["status"] == "ok")
    qualifier = (f"The connected service reports LLM use for {llm} rows; confirm against actual provider calls." if llm else
                 "No LLM usage was reported. This run does not establish an LLM benefit.")
    lines = ["# Raja observed evaluation results", "", warning, "", qualifier, "",
             f"{report['unique_case_count']} unique synthetic cases reused across {len(groups)} configurations; configuration rows are not independent samples.", "",
             "| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    for summary in summaries:
        lines.append("| " + " | ".join(text(summary[key]) for key in keys) + " |")
    lines += ["", "Detection means flagged for verification/review, not proven fraud. Denominators exclude pending cases and adapter failures.",
              "Proof-quality false alerts and legitimate physical-handoff review burden have different denominators.",
              "Scan-only unsupported acceptances are expected and expose the weak baseline; it cannot complete custody.",
              "Recommendation latency is measured for valid predictions. Human triage time and explanation quality remain unmeasured.",
              "Mandatory backend checks and final policy stay unchanged across investigation variants.",
              "The supplied Bhone bridge measures pure mandatory checks/policy with a synthetic clock. It makes no database/API/approval writes or follow-up tool calls.",
              "Investigation profiles are inactive for that rules-only bridge; repeated profile rows are not a measured profile tradeoff.", "", "## Observed misses", ""]
    for summary in summaries:
        misses = ", ".join(summary["missed_case_ids"]) if summary["evaluated_cases"] else "not measured"
        lines.append(f"- {summary['profile']} / {summary['mode']}: {misses or 'none'}. Pending: {summary['awaiting_pipeline']}; errors: {summary['adapter_errors']}.")
    lines += ["", "C20 has invalid hidden truth with consistent observable records; a real pipeline may also miss it.",
              "C12/C19 model legitimate physical handoffs with insufficient proof. Keeping them pending does not prove fraud.",
              "Validation against uploaded backend/models.py does not establish that schema.md is locked. API latency, a real LLM ablation, protected production data and a real pilot are separate checks."]
    (folder / "observed_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    table = "".join("<tr>" + "".join("<td>" + html.escape(text(summary[key])) + "</td>" for key in keys) + "</tr>" for summary in summaries)
    details = "".join("<details><summary>" + html.escape(f"{row['case_id']} / {row['profile']} / {row['mode']} / {row['status']}") +
                      "</summary><pre>" + html.escape(json.dumps(row, indent=2)) + "</pre></details>" for row in rows)
    page = ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Raja zer0Track evaluation</title><style>body{font:16px/1.5 system-ui;max-width:1150px;margin:30px auto;padding:0 20px;color:#15222e}'
            'table{border-collapse:collapse;width:100%}td,th{border:1px solid #d9d9d9;padding:9px;text-align:left}th{background:#edf2f6}'
            '.scroll{overflow:auto}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fa;padding:12px}details{padding:8px;border-bottom:1px solid #d9d9d9}</style>'
            '<h1>Raja zer0Track evaluation</h1><p>' + html.escape(warning) + '</p><p><strong>' + html.escape(qualifier) + '</strong></p>'
            '<p>Pending and failed calls receive no detection credit. Scan-only is an intentionally weak baseline. All cases are synthetic.</p>'
            '<div class="scroll"><table><tr>' + ''.join('<th>' + heading + '</th>' for heading in headers) + '</tr>' + table + '</table></div>'
            '<p>See observed_results.md for denominators, misses and limitations. Human review time is unmeasured.</p><h2>Observed case records</h2>' + details + '</html>')
    (folder / "report.html").write_text(page, encoding="utf-8")
    return report


def _selected(value, choices):
    names = list(choices) if value == "all" else value.split(",")
    if not names or len(set(names)) != len(names) or any(name not in choices for name in names):
        raise ValueError("Use 'all' or comma-separated values from " + ", ".join(choices))
    return names


def main(argv=None):
    parser = argparse.ArgumentParser(description="Raja only: scan baseline and team-backed rules/LLM comparisons.")
    parser.add_argument("--cases", type=Path, default=ROOT / "data" / "dataset")
    parser.add_argument("--labels", type=Path, default=ROOT / "evaluation" / "private_labels.json")
    parser.add_argument("--out", type=Path, default=ROOT / "evaluation" / "results")
    parser.add_argument("--modes", default="all")
    parser.add_argument("--profiles", default="all")
    parser.add_argument("--pipeline", help="Team's evaluation adapter module:function; no adapter is fabricated")
    parser.add_argument("--backend", action="store_true", help="Connect the supplied Bhone pure checkers/policy; real LLM arm stays pending")
    parser.add_argument("--draft", action="store_true", help="Allow a report with full-check arms marked awaiting_pipeline")
    parser.add_argument("--fail-on-unsafe", action="store_true")
    args = parser.parse_args(argv)
    try:
        records = load_dataset(args.cases, args.labels)
        modes, profiles = _selected(args.modes, MODES), _selected(args.profiles, POLICY_VARIANTS)
        pipeline = None
        if args.backend and args.pipeline:
            raise ValueError("Choose --backend or a custom --pipeline, not both.")
        if args.backend:
            pipeline = evaluate_with_bhone
        elif args.pipeline:
            if ":" not in args.pipeline:
                raise ValueError("Use module:function for the integration adapter.")
            module_name, function_name = args.pipeline.split(":", 1)
            pipeline = getattr(importlib.import_module(module_name), function_name)
            if not callable(pipeline):
                raise ValueError("Pipeline target is not callable.")
        if pipeline is not None:
            if any(record["input"]["schema_validation"][key] != "model_validated" for record in records for key in ("shipment_event", "courier_history")):
                raise ValueError("Validate inputs against the actual team models before CLI pipeline evaluation. Use --require-team-schema when generating.")
        rows = []
        for profile in profiles:
            for mode in modes:
                rows.extend(run_configuration(records, mode, pipeline=pipeline, policy=profile))
    except (ValueError, OSError, ImportError, AttributeError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    report = write_results(rows, args.out, dataset=records)
    for summary in report["summary"]:
        print(f"{summary['profile']} / {summary['mode']}: evaluated={summary['evaluated_cases']}, pending={summary['awaiting_pipeline']}, errors={summary['adapter_errors']}")
    print(f"Report: {args.out / 'report.html'}")
    if any(row["status"] == "adapter_error" for row in rows):
        return 2
    if any(row["status"] == "awaiting_pipeline" for row in rows) and not args.draft:
        print("Full comparison awaits the real pipeline. Use --draft to save an explicitly pending draft report.")
        return 2
    if args.fail_on_unsafe and any(row["protocol_violations"] for row in rows if row["mode"] != "scan_only"):
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
