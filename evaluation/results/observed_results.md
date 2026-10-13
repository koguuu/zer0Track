# Raja observed evaluation results

Synthetic evaluation. Pending pipeline rows: 0; adapter errors: 0.

No LLM usage was reported. This run does not establish an LLM benefit.

20 unique synthetic cases reused across 9 configurations; configuration rows are not independent samples.

| Profile | Mode | Evaluated | Detection % | False alerts % | Unsupported accepts | Unresolved | Tools | Mean ms |
|---|---|---|---|---|---|---|---|---|
| strict | scan_only | 20 | 7.14 | 0.0 | 19 | 0 | 0 | 0.0423 |
| strict | rules_only | 20 | 92.86 | 33.33 | 0 | 7 | 0 | 14.2163 |
| strict | rules_and_llm | 20 | 92.86 | 33.33 | 0 | 7 | 9 | 14.2092 |
| balanced | scan_only | 20 | 7.14 | 0.0 | 19 | 0 | 0 | 0.0465 |
| balanced | rules_only | 20 | 92.86 | 33.33 | 0 | 7 | 0 | 0.1801 |
| balanced | rules_and_llm | 20 | 92.86 | 33.33 | 0 | 7 | 15 | 15.1424 |
| loose | scan_only | 20 | 7.14 | 0.0 | 19 | 0 | 0 | 0.0447 |
| loose | rules_only | 20 | 92.86 | 33.33 | 0 | 7 | 0 | 0.1247 |
| loose | rules_and_llm | 20 | 92.86 | 33.33 | 0 | 7 | 15 | 11.0106 |

Detection means flagged for verification/review, not proven fraud. Denominators exclude pending cases and adapter failures.
Proof-quality false alerts and legitimate physical-handoff review burden have different denominators.
Scan-only unsupported acceptances are expected and expose the weak baseline; it cannot complete custody.
Recommendation latency is measured for valid predictions. Human triage time and explanation quality remain unmeasured.
Mandatory backend checks and final policy stay unchanged across investigation variants.
The supplied Bhone bridge measures pure mandatory checks/policy with a synthetic clock. It makes no database/API/approval writes or follow-up tool calls.
Investigation profiles are inactive for that rules-only bridge; repeated profile rows are not a measured profile tradeoff.

## Observed misses

- strict / scan_only: C04, C06, C07, C08, C09, C10, C11, C12, C15, C16, C18, C19, C20. Pending: 0; errors: 0.
- strict / rules_only: C20. Pending: 0; errors: 0.
- strict / rules_and_llm: C20. Pending: 0; errors: 0.
- balanced / scan_only: C04, C06, C07, C08, C09, C10, C11, C12, C15, C16, C18, C19, C20. Pending: 0; errors: 0.
- balanced / rules_only: C20. Pending: 0; errors: 0.
- balanced / rules_and_llm: C20. Pending: 0; errors: 0.
- loose / scan_only: C04, C06, C07, C08, C09, C10, C11, C12, C15, C16, C18, C19, C20. Pending: 0; errors: 0.
- loose / rules_only: C20. Pending: 0; errors: 0.
- loose / rules_and_llm: C20. Pending: 0; errors: 0.

C20 has invalid hidden truth with consistent observable records; a real pipeline may also miss it.
C12/C19 model legitimate physical handoffs with insufficient proof. Keeping them pending does not prove fraud.
Validation against uploaded backend/models.py does not establish that schema.md is locked. API latency, a real LLM ablation, protected production data and a real pilot are separate checks.
