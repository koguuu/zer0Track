# Agent output contract

Source of truth: `backend/models.py` and `agent/models.py`. `schema.md` is the
old v1 draft and no longer matches the code. This file documents what the agent
produces and where each consumer reads it. Everything below was checked against
the code on 2026-10-13.

## 1. Canonical shape: `InvestigationResult` (`agent/models.py`)

Produced by `run_investigation()`. Strict (`extra="forbid"`). Advisory only: it
never changes a check result or the decision.

| Field | Type | Meaning |
|---|---|---|
| `shipment_id` | str | Case this belongs to |
| `unresolved_check` | str or null | First check that was not PASS (`parcel_code_integrity`, `identity_authority`, `location`) |
| `tool_call` | `ToolCallRecord` or null | The single lookup that ran (at most one) |
| `reasoning` | str | Plain-text explanation; may contain a truncated notes preview (200 chars) |
| `unresolved_questions` | list[str] | What is still open |
| `suggests_resolved` | bool | Agent opinion only; the backend re-derives its own answer |
| `injection_signal` | `InjectionSignal` or null | Advisory notes classification; null when no LLM key, no notes, notes withheld, or the classifier failed |

`ToolCallRecord`: `tool_name` (`lookup_delegation` / `lookup_checkpoint` / `lookup_custody_history`), `arguments` (dict of str), `called_at`, `succeeded` (bool), `result_summary`, `evidence_ids`.

`InjectionSignal`: `flagged` (bool), `category` (`none` / `fake_approval_claim` / `instruction_to_skip_check` / `forged_system_text` / `other_manipulation_attempt`), `confidence` (0 to 1).

## 2. Where the dashboard reads it: `GET /cases/{shipment_id}/audit`

Needs the service key. Returns:

```json
{ "chain_valid": true,
  "events": [ { "seq": 2, "shipment_id": "...", "kind": "AGENT_INVESTIGATION",
                "recorded_at": "2026-10-13T15:07:52+00:00",
                "payload": { ...InvestigationResult as above... },
                "prev_hash": "...", "entry_hash": "...", "mac": "..." } ] }
```

`kind` is one of `EVALUATION`, `RECHECK`, `APPROVAL`, `AGENT_INVESTIGATION`.

- `chain_valid` is for the whole chain, not just this shipment.
- A case that has not been investigated has no `AGENT_INVESTIGATION` event. The timeline should show "Not yet investigated", not an error.
- `payload` for the other kinds has different keys (for example `EVALUATION` has `case_version`, `check_results`, `event_id`, `policy_decision`).

## 3. Vocabulary map (three names for the same things)

| Concept | Backend / dashboard | Evaluation adapter (`kogu_adapter.py`) |
|---|---|---|
| Decision | `decision`: `ACCEPT_READY`, `REQUEST_VERIFICATION`, `RECOMMEND_HOLD`, `ESCALATE` | `action`: `ACCEPT`, `VERIFY`, `HOLD`, `ESCALATE` |
| Check name | `parcel_code_integrity`, `identity_authority`, `location` | `parcel_code`, `identity`, `location` |
| Check result | `status`: `PASS` / `FAIL` / `UNKNOWN` | same strings, as a dict `{name: status}` |
| Tool call | `tool_call.tool_name`, `succeeded` | `tool_calls[].name`, `status` `OK` / `ERROR` |
| Supervisor action | `accept` / `hold` / `reject` / `escalate` (`ApprovalRequest`) | not used |

So `action` and `decision` are the same thing: `action` is only Raja's harness vocabulary, mapped from `decision` by `_ACTIONS` in `kogu_adapter.py`. The dashboard should use `decision`.

`confidence`: `AgentDecision` has none. `CheckerResult.confidence` is optional and legacy; the backend never sets it. The only live confidence is `InjectionSignal.confidence`.

## 4. Open item: nothing triggers an investigation

`investigate_and_maybe_recheck()` is called only from tests. There is no endpoint and no background job, so the dashboard cannot start an investigation, and the audit trail only contains `AGENT_INVESTIGATION` events if something ran it against the same database.

Proposal (not implemented, not tested here because FastAPI was not installable in the review environment): a service-key endpoint in `backend/api.py`.

```python
@app.post("/cases/{shipment_id}/investigate")
def investigate(shipment_id: str, _auth: None = Depends(service_auth),
                store: Store = Depends(get_store)):
    from agent.orchestrator import investigate_and_maybe_recheck  # lazy: agent imports backend
    case = store.get_case(shipment_id)
    out = investigate_and_maybe_recheck(case, store, llm_client=None)  # pick client as in kogu_adapter
    return {"audit_logged": out.audit_logged,
            "recheck_requested": out.recheck_requested,
            "investigation": out.investigation.model_dump(mode="json")}
```

Points to settle before building it: who owns the edit (`api.py` is Bhone's), whether the dashboard calls it from a button or automatically when a case is opened, and that the orchestrator's recheck makes an HTTP call back to the same server.

## 5. Dataset wrapper (every file in `data/dataset/`)

Exactly these top-level keys: `fixture_format` (`raja-draft-v2`), `case_id`, `source` (`synthetic`), `schema_validation`, `shipment_event` (a `ShipmentEvent`), `courier_history` (a `CourierHistory`), `fixture_context` (`reference_time`, `trusted_shipment`, `transfer_code`, `authenticated_handler_id`, `lookups`, `photo_metadata`, `baseline_scan`). All 20 files validate against `backend/models.py`.
