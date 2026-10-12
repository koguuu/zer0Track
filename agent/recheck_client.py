"""The one HTTP call the agent side is allowed to make: POST
/cases/{id}/recheck. This file is deliberately the ONLY place in the agent
package holding network code or the service credential, so investigator.py
and tools.py stay pure, synchronous, and easy to unit test without a live
backend.

Security notes:
  - Uses a dedicated ZTRACK_SERVICE_KEY, never the courier or supervisor
    credentials used elsewhere in backend/api.py. If this credential leaks,
    the blast radius is "can ask the backend to re-run its own deterministic
    checks against its own already-trusted data" — it cannot approve a
    case, submit new evidence, or write to the trusted manifest.
  - The request body is RecheckRequest: expected_case_version (an int) and
    a short, templated reason string. The agent's actual reasoning,
    unresolved_questions, and tool_call are deliberately never sent — the
    backend does not trust them and this client does not give it the
    chance to. See agent/orchestrator.py for how the reason is built.
  - Bounded by RECHECK_TIMEOUT_SECONDS; a hung or unreachable backend raises
    RecheckUnavailable rather than blocking indefinitely, mirroring the
    tool-call timeout behavior in tools.py.
"""
from __future__ import annotations

import os

import requests

RECHECK_TIMEOUT_SECONDS = 5.0


class RecheckUnavailable(Exception):
    """Raised when a recheck request could not be made or did not succeed.
    Callers (agent/orchestrator.py) must treat this exactly like any other
    investigation-side failure: report it, fall through, never retry in a
    loop against a backend that just rejected or failed to answer.
    """


def request_recheck(shipment_id: str, expected_case_version: int, reason: str) -> dict:
    """POSTs to /cases/{shipment_id}/recheck using ZTRACK_API_BASE_URL and
    ZTRACK_SERVICE_KEY from the environment. Returns the parsed CaseView
    JSON on success (HTTP 200); raises RecheckUnavailable otherwise,
    including on missing configuration, network failure, or any non-200
    response (a 409 from a stale version or a non-pending case is a
    legitimate, expected outcome here, not something to retry around).
    """
    base_url = os.environ.get("ZTRACK_API_BASE_URL")
    service_key = os.environ.get("ZTRACK_SERVICE_KEY")
    if not base_url or not service_key:
        raise RecheckUnavailable(
            "ZTRACK_API_BASE_URL and/or ZTRACK_SERVICE_KEY are not configured for this process"
        )
    try:
        response = requests.post(
            f"{base_url.rstrip('/')}/cases/{shipment_id}/recheck",
            json={"expected_case_version": expected_case_version, "reason": reason},
            headers={"X-Service-Key": service_key},
            timeout=RECHECK_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise RecheckUnavailable(f"recheck request failed: {exc.__class__.__name__}") from exc
    if response.status_code != 200:
        raise RecheckUnavailable(f"recheck rejected: HTTP {response.status_code} {response.text[:200]}")
    return response.json()
