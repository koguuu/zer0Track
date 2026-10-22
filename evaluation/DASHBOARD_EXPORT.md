# Dashboard comparison export — Raja / Phase 2

Updated: 22 October 2026.

`dashboard_comparison.json` is a compact, offline projection of the existing
evaluation report for Nour's rules-only-versus-agent comparison view. It uses
stored results and does not call the backend, run an agent, contact an LLM,
approve a handoff or change any live case.

## Generate the export

Run from the repository root with Python 3.11 or later. The exporter uses only
the standard library and `evaluation/metrics.py`; it needs no API credentials.

```bash
python -m evaluation.export_dashboard
python -m evaluation.export_dashboard --check
python -m unittest evaluation.test_export_dashboard -v
```

The default input is `evaluation/results/results.json`. The default output is
`evaluation/results/dashboard_comparison.json`. Relative custom paths are
resolved from the current working directory:

```bash
python -m evaluation.export_dashboard --input evaluation/results/results.json --output evaluation/results/dashboard_comparison.json --pretty
```

Regenerate the export after regenerating evaluation results. `--pretty` is
optional; the default output is compact JSON. The input cannot be overwritten
by the exporter. Validation happens before writing the output.

`--check` is read-only: it recomputes the exact input-byte hash and validates the
existing export's contents without changing either file. A stale hash or stale
comparison data causes a non-zero exit. Run it before committing the export.

### Exact-byte provenance across Windows and Unix

The root `.gitattributes` pins `evaluation/results/results.json` and
`evaluation/results/dashboard_comparison.json` to LF line endings. Both JSON
writers also explicitly write LF on Windows. This keeps the input bytes stable
between the working copy and the committed Git blob. The recorded hash remains
the SHA-256 of the bytes actually read; the exporter does not normalize the
input before hashing or silently substitute a hash of equivalent JSON data.

For an older Windows working copy that still contains CRLF, normalize just the
source line endings before regenerating the export:

```bash
python -c "from pathlib import Path; p = Path('evaluation/results/results.json'); p.write_bytes(p.read_bytes().replace(b'\r\n', b'\n'))"
python -m evaluation.export_dashboard
python -m evaluation.export_dashboard --check
```

Include `.gitattributes`, both JSON files and the writer changes in the same
commit. After any evaluation rerun, regenerate and check the comparison export.

## Schema version 1

| JSON path | Meaning |
|---|---|
| `schema_version` | Integer `1`; consumers should reject unsupported versions. |
| `metadata.evaluation_generated_at` | Original evaluation timestamp; preserved from the source. |
| `metadata.exported_at` | Export creation timestamp in UTC. |
| `metadata.source` | `synthetic`. |
| `metadata.source_sha256` | SHA-256 of the exact input file bytes, for provenance. |
| `metadata.dataset_sha256` | Dataset hash copied from the source, or `null` when unavailable. |
| `metadata.unique_case_count` | Distinct synthetic cases, not repeated configuration rows. |
| `metadata.source_configuration_row_count` | All source rows, including scan-only when present. |
| `metadata.exported_configuration_row_count` | Rows for the two comparison modes across the three profiles. |
| `metadata.llm_usage_is_service_reported` | `true`; not independent proof of provider calls. |
| `metadata.operator_review_time_measured` | Copied measurement flag; `false` in this snapshot. |
| `default_profile` | `balanced`. |
| `mode_labels` | `rules_only`: Rules only; `rules_and_llm`: Agent-assisted. |
| `profiles.<profile>.summary.<mode>` | Precomputed mode metrics for cards and charts. |
| `profiles.<profile>.comparison` | Counts of successful pairs, unavailable pairs and changed recommendations. |
| `profiles.<profile>.cases` | Case results paired by case ID within the profile. |
| `limitations` | Short interpretation notes suitable for an expandable panel. |

Profiles are `strict`, `balanced` and `loose`. Only `rules_only` and
`rules_and_llm` are exported. The full source retains the scan-only baseline.

## Mode summaries

All count fields are integers. Rates are percentages from 0 to 100, rather than
fractions. A rate with no eligible denominator is `null`.

| Field(s) | Definition / display |
|---|---|
| `total_cases`, `evaluated_cases`, `coverage_percent` | All rows versus rows with `status == "ok"`; coverage is evaluated / total. |
| `awaiting_pipeline`, `adapter_errors` | Pending and failed rows; keep these visible. |
| `flagged_invalid_proofs`, `invalid_proof_cases_evaluated`, `invalid_proof_detection_percent` | Show numerator / denominator alongside the percentage. A successful invalid-proof case is flagged when its action is not `ACCEPT`. |
| `missed_invalid_proofs` | Successful invalid-proof cases recommended `ACCEPT`. |
| `false_alerts`, `valid_proof_cases_evaluated`, `false_alert_percent` | Non-`ACCEPT` outcomes among successful valid-proof cases. |
| `unsupported_acceptances` | Successful `ACCEPT` outcomes with any mandatory check not `PASS`. |
| `unresolved_cases` | Successful `VERIFY` or `REVIEW` recommendations, following the existing evaluator definition. |
| `mean_time_to_recommendation_ms` | Mean measured recommendation latency over successful rows, in milliseconds; `null` if none succeeded. |
| `legitimate_handoff_review_burden_percent` | Share of successful physically legitimate handoffs receiving a non-`ACCEPT` action; this uses a different denominator from the valid-proof false-alert rate. |
| `agent_invoked_cases` | Successful rows where the adapter reported `agent_invoked == true`. |
| `tool_calls` | Total reported tool calls across successful rows. |
| `llm_cases_service_reported` | Successful rows reporting LLM use; counts cases, not provider requests or tokens. |
| `protocol_violation_cases` | Successful rows with one or more reported protocol violations; show as an evaluation warning. |

The exporter recomputes these metrics with `evaluation/metrics.py` and rejects
the input if the stored summaries disagree. It does not award pending or failed
rows detection credit.

## Case records

Each case contains `case_id`, a synthetic scenario `title`, `modes`, and
`decision_changed`. Under each mode:

| Field | Meaning |
|---|---|
| `status` | `ok`, `awaiting_pipeline`, or `adapter_error`. |
| `observed_action` | Existing evaluation recommendation vocabulary. |
| `checks` | `parcel_code`, `identity`, `location`: `PASS`, `FAIL`, or `UNKNOWN`. |
| `latency_ms` | Measured recommendation latency. |
| `agent_invoked` | Whether the agent was invoked. |
| `llm_used_service_reported` | Whether the adapter reported LLM use for this case. |
| `tool_call_count`, `tools` | Count plus projected tool names and `OK`/`ERROR`/`REJECTED` statuses. |
| `protocol_violation_count` | Number of reported violations; raw strings are omitted. |

For unsuccessful rows, all result-dependent scalar fields and `checks` are
`null`, while `tools` is empty. Use `status` to render "Unavailable" or "Pending";
an empty tool list on an unsuccessful row does not establish zero attempted
calls. Missing results must not become successful zero values in the UI.

`decision_changed` is a boolean only when both modes succeeded; otherwise it is
`null`. At profile level, `comparison` contains:

- `total_case_count`;
- `paired_successful_case_count`;
- `uncompared_case_count`;
- `changed_decision_count`, which is `null` when no successful pairs exist.

The evaluation vocabulary maps to live backend vocabulary as follows. Keep
evaluation recommendations separate from actual approval/custody status.

| Evaluation `observed_action` | Backend decision equivalent |
|---|---|
| `ACCEPT` | `ACCEPT_READY` |
| `VERIFY` | `REQUEST_VERIFICATION` |
| `HOLD` | `RECOMMEND_HOLD` |
| `ESCALATE` | `ESCALATE` |
| `REVIEW`, `REJECT` | Harness-supported values; no automatic live-decision mapping is defined here. |

The export uses an explicit field projection. Raw notes, explanations, error
messages, tool arguments, private expected actions and per-case ground-truth
labels are excluded. Aggregated benchmark metrics still rely on the evaluator's
private labels. This does not implement Kogu's separate live-notes redaction task.

## Nour integration example

Read the file relative to the project, rather than the process working directory:

```python
import json
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "evaluation/results/dashboard_comparison.json"
comparison = json.loads(path.read_text(encoding="utf-8"))
if comparison["schema_version"] != 1:
    raise ValueError("Unsupported comparison schema")

profile = comparison["profiles"][comparison["default_profile"]]
rules = profile["summary"]["rules_only"]
agent = profile["summary"]["rules_and_llm"]
cases = profile["cases"]
```

This path example assumes code in `dashboard/app.py` or `dashboard/zt_data.py`.
The dashboard can render summaries directly and use `cases` for paired outcomes.
The mode toggle selects stored data. Display the original evaluation timestamp,
synthetic-data label, denominators and unavailable values. Label evaluation
invocation counts separately from the live "Agent investigations run" metric.
Use plain text for scenario titles and tool names. No dashboard code is changed
by this handoff; Nour can wire it into the planned view.

## Included snapshot and verification

The included source evaluation was generated on **13 October 2026**. Exporting
it on 22 October does not make it a fresh pipeline evaluation.

It contains 20 unique synthetic cases, 180 source configuration rows and 120
exported rows. The generated compact file is 44,594 bytes, compared with 538,919
bytes for the source (91.73% smaller).

The source is byte-for-byte identical to `evaluation/results/results.json` in
the supplied repository's HEAD (`7551867`), with SHA-256
`60533a1e8a40706f5bdf3dd536b68720458d75f4c35aabfcfa3d4825bdc094ff`.
The older 554,814-byte Windows copy used CRLF, which added 15,895 bytes and
produced a different hash despite identical parsed evaluation data. This fix
restores the committed LF bytes and regenerates the export; it does not rerun
or change the evaluation measurements.

| Profile | Successful pairs | Changed recommendations | Agent-invoked cases | Tool calls | Service-reported LLM cases |
|---|---:|---:|---:|---:|---:|
| strict | 20 | 0 | 9 | 9 | 0 |
| balanced | 20 | 0 | 15 | 15 | 0 |
| loose | 20 | 0 | 15 | 15 | 3 |

Both modes flag 13/14 invalid proofs (92.86%) and 2/6 valid proofs (33.33%) in
each profile. There are zero pending rows and adapter errors in the included
source. These are completed evaluations, not a claim that all predictions
matched private expected answers. The additional lookups did not change the
recommendations in this snapshot; improved accuracy or operator time is not
established.

Eleven focused exporter tests passed. They cover correct pair/metric handling,
LLM-versus-agent usage distinctions, exclusion of raw nested fields, source
immutability, duplicate/missing pairs, summary mismatches, failed/pending pairs,
changed recommendations, non-finite timing, input hashes, overwrite refusal,
CRLF input preservation, LF output and rejection of newline-only provenance drift.
Existing backend, agent and dashboard behavior was not re-benchmarked by this
export task.
