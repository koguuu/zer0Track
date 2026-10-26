# Backend security upgrades (synthetic demo)

## Geofence
`GET /cases/{shipment_id}/geofence` requires `X-Service-Key` and returns trusted approved checkpoints for the case handoff type, including `latitude`, `longitude`, `radius_m`, and measured separation from the **untrusted** reported GPS location. `within_radius` is display-only: the authoritative three-valued `location_check_status` is taken from the persisted deterministic checker, which also checks GPS freshness, accuracy and mock flags. An in-radius result is **not proof of presence**.

## Two-person approval
For `reject` or `escalate`, first call to `POST /cases/{id}/approve` with `X-Operator-Id`, `X-Supervisor-Token`, and an `ApprovalRequest` leaves the case `PENDING`, writes an audit proposal and returns that pending case. A **different** supervisor must call `/approve` with the same action and current version (new request ID) to finalize. Second signature identity is authenticated by existing supervisor credential headers. First signatures are cleared on recheck or case finalization. `accept` and `hold` retain their former single-approval policy. The response schema remains `CaseView` for compatibility; the UI can distinguish pending by `state`. `GET /cases/{id}/approvals/pending` (service key) returns the proposed action, operator and timestamp for the two-person approval UI.

## Rate limit
`POST /cases/{id}/evaluate`: 10 authenticated requests per courier per rolling 60 seconds, backed by SQLite under an immediate transaction for cross-process consistency. The 11th returns 429. Even idempotent repeated calls count toward flood protection. Adjust policy parameters on `Store.enforce_courier_rate_limit` for the demo.

## Audit tamper demo
Set `ZTRACK_AUDIT_HMAC_KEY` to a secret 32+ characters long. With seeded cases: `python -m scripts.audit_security_demo --db ./var/zer0track.sqlite`. It takes SQLite's online backup and tampers **only** with a temporary copied DB; the original is never mutated. Expected original `True`, tampered `False`.

## Independent audit anchor
`python -m scripts.audit_anchor export --db ./var/zer0track.sqlite --anchor /secure/external/anchor.json` exports an HMAC-authenticated hash-chain head. `python -m scripts.audit_anchor verify --db ... --anchor ...` verifies it. A local file is **not automatically external**: copy it to separate restricted storage / another trust domain (e.g. read-only object store) and securely retain an independent snapshot. The supplied CLI does **not** publish to a cloud service. Do not overwrite/delete the independent historical anchor; archive each checkpoint before replacing the local file. Keep signing key separate, never commit it.

## Authentication limitations
`/auth/login`, `/auth/verify` and `/auth/logout` provide demo sessions, but existing service and supervisor APIs retain their separate headers. A dashboard login screen alone cannot guard direct API access. Integrate the staff session into server-side dashboard access control and independently review service/supervisor authorization before production. Demo credentials and local SQLite are not production IAM.

## Suggested review
Run `python -m unittest evaluation.test_backend` and `python -m unittest tests.test_security_upgrades`; do **not** run `evaluation.run_eval` since it may overwrite team evaluation results.
