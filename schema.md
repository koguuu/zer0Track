# Handoff Trust Verifier — Shared Data Schema (Draft v1)

Lock this in the team call, then everyone builds against it. Change it only by agreement — downstream code depends on these shapes.

---

## 1. Shipment Event (GS1 EPCIS-inspired)

Each shipment is a list of events from pickup to delivery. This is the input to the agent.

```json
{
  "shipment_id": "GDX1029384756",
  "events": [
    {
      "event_id": "evt_001",
      "type": "pickup",
      "timestamp": "2026-10-10T09:02:00+08:00",
      "location": { "lat": 3.1073, "lng": 101.6067, "label": "PJ Hub" },
      "courier_id": "CR-2291",
      "notes": null
    },
    {
      "event_id": "evt_002",
      "type": "out_for_delivery",
      "timestamp": "2026-10-10T13:15:00+08:00",
      "location": { "lat": 3.1073, "lng": 101.6067, "label": "PJ Hub" },
      "courier_id": "CR-2291",
      "notes": null
    },
    {
      "event_id": "evt_003",
      "type": "delivery_confirmed",
      "timestamp": "2026-10-10T13:52:00+08:00",
      "location": { "lat": 3.1180, "lng": 101.6400, "label": "Recipient GPS at confirmation" },
      "courier_id": "CR-2291",
      "confirmation_method": "TAC_CODE",
      "confirmation_value_hash": "a1b2c3...",
      "photo_url": null,
      "notes": null
    }
  ],
  "recipient": {
    "address_label": "Unit 5-3, Jalan Example, Petaling Jaya",
    "address_lat": 3.1180,
    "address_lng": 101.6400,
    "expected_delivery_window": {
      "start": "2026-10-10T12:00:00+08:00",
      "end": "2026-10-10T18:00:00+08:00"
    }
  },
  "label": "normal"
}
```

**Event `type` values:** `pickup`, `hub_scan`, `out_for_delivery`, `delivery_confirmed`, `delivery_failed`, `customer_contact`

**`label` field** (only present in your synthetic dataset, not real-world): `"normal"`, `"fraud_gps_spoof"`, `"fraud_social_engineered_otp"`, `"fraud_ghost_delivery"`, `"ambiguous"` — used later to score the agent's accuracy.

---

## 2. Courier History (lookup data, not part of the shipment itself)

```json
{
  "courier_id": "CR-2291",
  "deliveries_last_30d": 412,
  "avg_handling_time_minutes": 38,
  "prior_flagged_incidents": 1,
  "flagged_incident_types": ["gps_mismatch"]
}
```

---

## 3. Checker Function Output (what each "tool" in workstream #3 returns)

Every checker takes a `shipment_id` (or the event list) and returns this shape, so the agent can call any of them uniformly:

```json
{
  "checker_name": "gps_consistency",
  "passed": false,
  "confidence": 0.82,
  "detail": "Delivery confirmation GPS is 4.1km from recipient address; expected under 300m."
}
```

Planned checkers (workstream #3 builds these):
- `gps_consistency` — delivery GPS vs. recipient address
- `timing_plausibility` — time between `out_for_delivery` and `delivery_confirmed` vs. route distance
- `courier_pattern` — this courier's recent flagged-incident rate
- `confirmation_integrity` — was the TAC/OTP entered in a context consistent with past behavior (e.g. unusually fast entry, repeated attempts)

---

## 4. Agent Decision Output (what workstream #2 produces, and what #4 and #5 consume)

```json
{
  "shipment_id": "GDX1029384756",
  "decision": "flag",
  "confidence": 0.78,
  "reasoning": "GPS at confirmation is 4.1km from the registered address, outside the normal delivery radius. Courier has one prior GPS-mismatch flag. Timing is otherwise consistent with the route.",
  "checkers_run": ["gps_consistency", "timing_plausibility", "courier_pattern"],
  "recommended_action": "request_recipient_verification",
  "requires_human_approval": true
}
```

**`decision` values:** `"approve"`, `"flag"`, `"escalate"`
**`recommended_action` values:** `"none"`, `"request_recipient_verification"`, `"hold_payment"`, `"escalate_to_investigation_team"`

---

## 5. Approval Record (workstream #4 — audit trail)

```json
{
  "approval_id": "appr_001",
  "shipment_id": "GDX1029384756",
  "agent_decision": "flag",
  "reviewed_by": "ops_user_17",
  "review_action": "approved_hold",
  "reviewed_at": "2026-10-10T14:05:00+08:00",
  "comment": "Called recipient, confirmed address mismatch — parcel redirected."
}
```

---

## Why this unblocks everyone

- **Bhone (backend)** generates files matching the Shipment Event schema (section 1) + Courier History (section 2), and builds the checkers to return section 3 shape.
- **Kogu (agent)** calls the checkers (mocked at first, real once Bhone ships them) and outputs section 4.
- **Raja (data/eval)** generates synthetic data matching section 1/2 and scores decisions against section 4.
- **Nour (dashboard)** builds against mock section 4 + section 5 output immediately — doesn't wait on the real agent.
- **Win (integration)** keeps everyone's output honest against this file as the pieces get wired together.

Agree this schema (or tweak it) in a 30-min call before building — then everyone works in parallel with almost no blocking.
