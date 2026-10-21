# Raja validation with Bhone's uploaded backend

> **Status update (2026-10-14):** This document predates Kogu's agent/LLM
> integration and is now stale on several points: "Rules+LLM rows awaiting
> Kogu", "no measured LLM advantage... yet", and "Actual LLM calls | 0" in
> the table below no longer reflect the current state. The real pipeline is
> connected (`evaluation/kogu_adapter.py:pipeline`), the full `rules_and_llm`
> arm evaluates all 20 cases with zero pending rows, and a live run with a
> configured Groq key has recorded real LLM classifier calls
> (`llm_cases_service_reported` non-zero). See `evaluation/results/` for the
> current numbers; this file is kept as a historical record of the earlier
> integration pass.

Tested on 11 October 2026 with Python 3.12.14 and Pydantic 2.13.5. Windows commands are supplied for Raja's Python 3.11 setup; no native Windows run was performed. The six uploaded backend files were tested unchanged. FastAPI route handling was not exercised; models, checkers, policy and SQLite Store were exercised directly.

Command: `python -m unittest evaluation.test_raja evaluation.test_backend -v`

Observed: **40 tests passed**: 28 generator/harness/metrics tests and 12 actual-backend tests. Checks include all 20 native inputs and trusted seeds, private-answer isolation, observed misses/false alerts, pending LLM results, protocol violations, guardhouse permissions, code/receiver binding, deterministic handling of injection text, non-mutating evaluation, retries, stale evidence versions, changed-event replay, failed-check acceptance rejection, single-use code consumption, two concurrent acceptance attempts and HMAC/hash-linked audit verification.

Approval tests use temporary synthetic databases and a synthetic test key. The normal evaluator uses pure checks/policy without approving any action. Harness response doubles are not used for benchmark results.

## Observed benchmark

Command: `python -m evaluation.run_eval --backend --draft --fail-on-unsafe`

| Observation | Count |
|---|---:|
| Unique synthetic cases | 20 |
| Invalid-proof / valid-proof labels | 14 / 6 |
| Baseline observations across three profiles | 60 |
| Real Bhone rules observations across three profiles | 60 |
| Rules+LLM rows awaiting Kogu | 60 |
| Actual LLM calls | 0 |
| Full-arm protocol violations | 0 |

| Per 20-case configuration | Scan-only | Bhone mandatory rules |
|---|---:|---:|
| Invalid proofs flagged / 14 | 1 | 13 |
| Detection | 7.14% | 92.86% |
| Invalid proofs missed | 13 | 1 (C20) |
| Valid proofs flagged / 6 | 0 | 2 (C13, C17) |
| False-alert rate | 0% | 33.33% |
| Acceptances lacking all mandatory PASS results | 19 | 0 |
| Unresolved VERIFY/REVIEW recommendations | 0 | 7 |
| Follow-up tool calls | 0 | 0 |

Profiles affect investigator controls and are inactive in the mandatory-only arm. Repetition is not independent sampling or a measured profile tradeoff. There is no measured LLM advantage, agent timeout recovery or model injection-security result yet.

Inputs validate against the uploaded model classes, including trusted manifest/code classes; schema_lock_confirmed remains false. Kogu's lookup contract is unverified. C14 permissions are present initially; C13/C17 require further evidence; C19's timeout is not invoked; C20 is the intended consistent-records miss.

Latency is model validation plus pure checks/policy and evaluation handling, not API/DB or operator triage time. No production accuracy or real pilot is established.

Separate probes reproduced backdated-evidence acceptance and rejection of valid +08:00 code timestamps at commit. These are outside the fixed benchmark. See BHONE_COMPATIBILITY.md; passing tests do not imply these backend issues are fixed.

Explicit draft generation also works with placeholder backend models. Three additional tests verify generation without model imports, honest not_checked metadata and refusal to combine draft mode with model validation. Connected backend CLI runs still require model-validated inputs.

A separate scaffold-only process test used an actually empty backend/models.py. Draft generation, pending evaluation and all 28 Raja unit tests passed. A connected --backend run correctly refused these unvalidated inputs.

# Raja validation with Bhone's uploaded backend

## Phase 2 export validation — 22 October 2026

Added `evaluation/export_dashboard.py`, the generated
`evaluation/results/dashboard_comparison.json`, and
[`DASHBOARD_EXPORT.md`](DASHBOARD_EXPORT.md) for Nour's comparison view.

`python -m unittest evaluation.test_export_dashboard -v` passed **9 exporter
tests**. `python -m evaluation.export_dashboard` successfully validated the
stored report, including its recomputed summary metrics and matching case sets.
It produced 20 case pairs per profile, 120 exported configuration rows, no
unavailable pairs and zero changed recommendations. The compact export is
44,594 bytes versus the original 554,814 bytes (91.96% smaller).

The stored report's original timestamp is 13 October 2026. Its 180 source rows
reuse 20 synthetic cases across nine configurations; they are not 180 independent
correct predictions. Both comparison modes flag 13/14 invalid proofs and 2/6
valid proofs in each profile. Service-reported LLM case counts are 0 for strict,
0 for balanced and 3 for loose; agent invocation is counted separately.

This task validates the export of the stored snapshot. It does not rerun the
backend, provider calls, existing application tests or live dashboard. The
original evaluation files are preserved. The source file hash is recorded in
the export for provenance. The historical integration notes below remain dated
records of earlier work.

## Historical integration notes

> **Status update (2026-10-14):** This document predates Kogu's agent/LLM
> integration and is now stale on several points: "Rules+LLM rows awaiting
> Kogu", "no measured LLM advantage... yet", and "Actual LLM calls | 0" in
> the table below no longer reflect the current state. The real pipeline is
> connected (`evaluation/kogu_adapter.py:pipeline`), the full `rules_and_llm`
> arm evaluates all 20 cases with zero pending rows, and a live run with a
> configured Groq key has recorded real LLM classifier calls
> (`llm_cases_service_reported` non-zero). See `evaluation/results/` for the
> current numbers; this file is kept as a historical record of the earlier
> integration pass.

Tested on 11 October 2026 with Python 3.12.14 and Pydantic 2.13.5. Windows commands are supplied for Raja's Python 3.11 setup; no native Windows run was performed. The six uploaded backend files were tested unchanged. FastAPI route handling was not exercised; models, checkers, policy and SQLite Store were exercised directly.

Command: `python -m unittest evaluation.test_raja evaluation.test_backend -v`

Observed: **40 tests passed**: 28 generator/harness/metrics tests and 12 actual-backend tests. Checks include all 20 native inputs and trusted seeds, private-answer isolation, observed misses/false alerts, pending LLM results, protocol violations, guardhouse permissions, code/receiver binding, deterministic handling of injection text, non-mutating evaluation, retries, stale evidence versions, changed-event replay, failed-check acceptance rejection, single-use code consumption, two concurrent acceptance attempts and HMAC/hash-linked audit verification.

Approval tests use temporary synthetic databases and a synthetic test key. The normal evaluator uses pure checks/policy without approving any action. Harness response doubles are not used for benchmark results.

## Observed benchmark

Command: `python -m evaluation.run_eval --backend --draft --fail-on-unsafe`

| Observation | Count |
|---|---:|
| Unique synthetic cases | 20 |
| Invalid-proof / valid-proof labels | 14 / 6 |
| Baseline observations across three profiles | 60 |
| Real Bhone rules observations across three profiles | 60 |
| Rules+LLM rows awaiting Kogu | 60 |
| Actual LLM calls | 0 |
| Full-arm protocol violations | 0 |

| Per 20-case configuration | Scan-only | Bhone mandatory rules |
|---|---:|---:|
| Invalid proofs flagged / 14 | 1 | 13 |
| Detection | 7.14% | 92.86% |
| Invalid proofs missed | 13 | 1 (C20) |
| Valid proofs flagged / 6 | 0 | 2 (C13, C17) |
| False-alert rate | 0% | 33.33% |
| Acceptances lacking all mandatory PASS results | 19 | 0 |
| Unresolved VERIFY/REVIEW recommendations | 0 | 7 |
| Follow-up tool calls | 0 | 0 |

Profiles affect investigator controls and are inactive in the mandatory-only arm. Repetition is not independent sampling or a measured profile tradeoff. There is no measured LLM advantage, agent timeout recovery or model injection-security result yet.

Inputs validate against the uploaded model classes, including trusted manifest/code classes; schema_lock_confirmed remains false. Kogu's lookup contract is unverified. C14 permissions are present initially; C13/C17 require further evidence; C19's timeout is not invoked; C20 is the intended consistent-records miss.

Latency is model validation plus pure checks/policy and evaluation handling, not API/DB or operator triage time. No production accuracy or real pilot is established.

Separate probes reproduced backdated-evidence acceptance and rejection of valid +08:00 code timestamps at commit. These are outside the fixed benchmark. See BHONE_COMPATIBILITY.md; passing tests do not imply these backend issues are fixed.

Explicit draft generation also works with placeholder backend models. Three additional tests verify generation without model imports, honest not_checked metadata and refusal to combine draft mode with model validation. Connected backend CLI runs still require model-validated inputs.

A separate scaffold-only process test used an actually empty backend/models.py. Draft generation, pending evaluation and all 28 Raja unit tests passed. A connected --backend run correctly refused these unvalidated inputs.
