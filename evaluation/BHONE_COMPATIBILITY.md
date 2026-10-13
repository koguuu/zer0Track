# Bhone backend compatibility and follow-up

> **Status update (2026-10-14):** Issues 1 and 2 below are **fixed** in the
> current `backend/checkers.py`/`backend/storage.py` on `main`.
> `check_location_consistency()` now checks absolute freshness against real
> server time (`checked_at`), not just the submitted receipt timestamp, so
> backdated-but-internally-consistent evidence no longer passes. Code
> validity in `check_parcel_code_integrity()` now compares parsed, timezone-aware
> `datetime` objects rather than raw ISO strings, and `storage.py`'s SQL only
> performs an atomic `consumed_at IS NULL` compare-and-swap — no string
> timestamp comparison remains. The "Receiver authentication boundary" and
> "Remaining integration decisions" sections below are still accurate as
> documented limitations (see `docs/THREAT_MODEL.md`), not bugs. The rest of
> this document is kept as a historical record of what was found and fixed.

Reviewed all six files in the uploaded backend.zip unchanged. Raja's payloads now validate against the actual Pydantic classes. A read-only bridge calls the supplied mandatory checks and policy. The combined 40 tests passed. schema.md and Kogu's investigator/tools remain pending.

## Reproduced issue 1: submitted timestamps can make old location look fresh

File/function: `backend/checkers.py`, `check_location_consistency()`.

The code uses `age = (shipment.receipt_timestamp - p.recorded_at).total_seconds()`. Both timestamps are submitted in ShipmentEvent. The integrity check rejects a future handoff but has no maximum past event age.

Probe: reference time 2026-10-11T04:00:00Z, valid active unused code; set event_timestamp and receipt_timestamp to 2026-10-04T04:00:00Z and location.recorded_at to 2026-10-04T03:59:40Z. Keep the manifest, handler, receiver and coordinates valid.

Observed: all three checks PASS and policy ACCEPT_READY. The GPS evidence is seven days old relative to verification, but only 20 seconds old relative to the submitted receipt.

Suggested fix for Bhone: define event-time versus verification-time freshness. For real-time acceptance use a server-trusted receive/verification clock, bound event upload age and GPS evidence age, and retain an injected clock for deterministic tests. Define a separate verification path for legitimate offline/delayed evidence. Add this exact regression after agreeing the intended policy.

## Reproduced issue 2: timezone strings reject a valid code during acceptance

File/function: `backend/storage.py`, `seed_trusted()` / atomic consume in `approve_case()`.

Seeds preserve original offsets with `.isoformat()`. The SQL update compares issued_at/expires_at text with a UTC now string. Strings with different offsets do not have chronological lexical ordering.

Probe: normal live-time fixture; represent issued_at as ten minutes before now and expires_at as fifteen minutes after now, using +08:00. Seed a fresh temporary DB, evaluate, and attempt synthetic supervisor acceptance.

Observed: evaluation ACCEPT_READY; approval **409: Code consumed or invalid during acceptance**. The equivalent UTC normal fixture passes the acceptance tests.

Suggested fix for Bhone: canonicalize all compared times to UTC with fixed precision, or store numeric timestamps. Normalize both storage and comparison paths; keep the atomic transaction. Reject naive datetimes at ingestion or define a clear UNKNOWN path. Add equivalent UTC/+08:00 regression fixtures.

## Receiver authentication boundary

The API authenticates the courier. The checker matches a claimed receiver_id to trusted recipient/delegate IDs; it has no authenticated-receiver argument and does not establish the physical recipient's identity.

C10 detects a recipient-bound phished code presented under an unauthorized receiver ID. If an impostor possesses that code and claims the authorized ID, the supplied evidence is indistinguishable from normal delivery and can return ACCEPT_READY. This limits the claim that OTP phishing is detected.

Use accurate wording: “Checks courier authentication, recorded receiver/delegate authority and code binding; does not independently establish the physical receiver's identity.” Stronger recipient authentication/acknowledgement would need an agreed trusted source and schema/API contract. Raja's evaluator should not invent it.

## Remaining integration decisions

| Item | Current behavior | Team decision |
|---|---|---|
| Missing vs denied receiver authority | An unlisted receiver is FAIL whenever a manifest exists; no partial-registry completeness field. | Represent unavailable evidence as UNKNOWN without weakening a confirmed denial. |
| Agent follow-up evidence | No supplied agent/gateway or public trusted-record update path. | Validated trusted lookup results, then checker/policy revalidation; never model-granted permission or overwritten statuses. |
| Approval display | ACCEPT_READY flag is false; Store acceptance still needs supervisor approval. | Distinguish recommendation from an authorized custody commit in the dashboard. |
| Verification requests | VERIFY is currently a recommendation; no outbound contact operation. | Which routine contacts are permitted and which changes require approval. |
| Photo/signature inputs | Photo ref is context; signed receipt is absent/unverified and unused by mandatory checks. | Scope claims to implemented checks or agree a real signature/receipt verifier. |
| Audit anchor | Local HMAC/hash-chain verification passes; optional external checkpoint not deployment-tested. | Protected key/checkpoint storage and behavior if a configured anchor is missing; use “tamper-evident”, not “tamper-proof”. |

The current Bhone arm is a mandatory-rules baseline with zero follow-up tools. For an ablation isolating adaptive LLM selection, add a fixed follow-up comparator with the same evidence/budget as the LLM. Comparing mandatory-only with an agent also measures the benefit of additional evidence.

Keep Bhone's files unchanged in Raja's merge. After schema.md is locked, reconcile the four input classes and CheckerResult/AgentDecision mappings, regenerate the cases and rerun tests. No real LLM ablation or language-model injection-security result exists yet.
