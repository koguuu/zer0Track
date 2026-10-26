# Raja observed evaluation results

Synthetic evaluation. Pending pipeline rows: 0; adapter errors: 0.

The connected service reports LLM use for 3 rows; confirm against actual provider calls.

20 unique synthetic cases reused across 9 configurations; configuration rows are not independent samples.

| Profile | Mode | Evaluated | Detection % | False alerts % | Unsupported accepts | Unresolved | Tools | Mean ms |
|---|---|---|---|---|---|---|---|---|
| strict | scan_only | 20 | 7.14 | 0.0 | 19 | 0 | 0 | 0.0428 |
| strict | rules_only | 20 | 92.86 | 33.33 | 0 | 7 | 0 | 5.2531 |
| strict | rules_and_llm | 20 | 92.86 | 33.33 | 0 | 7 | 9 | 19.3451 |
| balanced | scan_only | 20 | 7.14 | 0.0 | 19 | 0 | 0 | 0.0783 |
| balanced | rules_only | 20 | 92.86 | 33.33 | 0 | 7 | 0 | 0.2555 |
| balanced | rules_and_llm | 20 | 92.86 | 33.33 | 0 | 7 | 15 | 31.6932 |
| loose | scan_only | 20 | 7.14 | 0.0 | 19 | 0 | 0 | 0.1879 |
| loose | rules_only | 20 | 92.86 | 33.33 | 0 | 7 | 0 | 0.3729 |
| loose | rules_and_llm | 20 | 92.86 | 33.33 | 0 | 7 | 15 | 184.1757 |

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
