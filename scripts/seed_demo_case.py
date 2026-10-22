"""One-off helper: seed a real case from the synthetic dataset into the
backend's actual database, so curl/Invoke-RestMethod calls against a running
`uvicorn backend.api:app` (or the dashboard in live mode) have a real,
queryable case to hit.

This is NOT the same thing as the dashboard's GDX-100x rows -- those are
pure UI mock data hardcoded in dashboard/zt_data.py and are never written to
this database. Use this script to put a real case where the backend can
actually see it.

Writes to the SAME database the backend reads: ZTRACK_DB_PATH if set,
otherwise the backend's own default ./var/zer0track.sqlite, resolved
relative to the current working directory -- so run this from the same
directory, with the same env var (if any), that you launch uvicorn from.

Usage (from the repo root, same venv as the backend):
    python -m scripts.seed_demo_case C16
    python -m scripts.seed_demo_case          # seeds all four demo cases

Dataset case IDs this is meant for (see scripts/demo_scenario.py for the
story behind each one): C01 clean handoff, C09 identity mismatch, C16
prompt-injection-in-notes showcase, C19 stale/unresolved location evidence.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "data" / "dataset"
DEFAULT_CASES = ["C01", "C09", "C16", "C19"]


def _seed_one(store, case_id: str) -> str:
    from backend.models import ShipmentEvent, TransferCodeRecord, TrustedShipmentRecord

    fixture = json.loads((DATASET / f"{case_id}.json").read_text())
    ctx = fixture["fixture_context"]
    trusted = TrustedShipmentRecord.model_validate(ctx["trusted_shipment"])
    code = TransferCodeRecord.model_validate(ctx["transfer_code"])
    event = ShipmentEvent.model_validate(fixture["shipment_event"])
    store.seed_trusted(trusted, code)
    case = store.evaluate_case(event, ctx["authenticated_handler_id"])
    return case.shipment_id


def main(argv=None) -> int:
    sys.path.insert(0, str(ROOT))
    from backend.storage import Store

    db_path = os.environ.get("ZTRACK_DB_PATH", "./var/zer0track.sqlite")
    store = Store(db_path)

    case_ids = argv if argv else DEFAULT_CASES
    for case_id in case_ids:
        shipment_id = _seed_one(store, case_id)
        print(f"Seeded {case_id} -> shipment_id={shipment_id} (db: {db_path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
