# Raja validation with Bhone's uploaded backend

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
