"""Standalone CLI that independently re-verifies the tamper-evident audit
log's hash chain -- a demonstrable proof, not just a claim.

Wraps backend.audit.verify_audit_chain (Bhone's hash-chain + HMAC
verifier, already in the codebase) in a runnable command, so anyone --
a teammate, a judge -- can prove the audit trail hasn't been silently
edited, without reading SQL by hand.

Usage:
    export ZTRACK_AUDIT_HMAC_KEY="...same key the server used..."
    python3 -m scripts.verify_audit_log [--db PATH] [--shipment-id ID] [--anchor PATH]

Exit code 0 means the chain verified clean. Any non-zero exit means
tampering, corruption, or a verification error -- this never silently
passes.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from backend.audit import GENESIS, verify_audit_chain


def _connect(db_path: str) -> sqlite3.Connection:
    if not Path(db_path).exists():
        print(f"ERROR: no database at {db_path!r}", file=sys.stderr)
        sys.exit(2)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="./var/zer0track.sqlite",
                         help="Path to the SQLite database (default: ./var/zer0track.sqlite, "
                              "same default backend/api.py uses).")
    parser.add_argument("--shipment-id", default=None,
                         help="If set, also print the per-shipment audit trail for this ID "
                              "(human-readable, after the chain-wide verification).")
    parser.add_argument("--anchor", default=None,
                         help="Optional signed checkpoint file path (see backend.audit.export_checkpoint) "
                              "to additionally verify the chain hasn't been truncated/replaced wholesale.")
    args = parser.parse_args(argv)

    conn = _connect(args.db)
    try:
        row_count = conn.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]
    except sqlite3.OperationalError:
        print(f"ERROR: {args.db!r} has no audit_log table -- is this the right database?", file=sys.stderr)
        return 2

    try:
        ok = verify_audit_chain(conn, anchor_path=args.anchor)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("(Set ZTRACK_AUDIT_HMAC_KEY to the same key the server used to write this log.)",
              file=sys.stderr)
        return 2

    print(f"Database: {args.db}")
    print(f"Total audit entries: {row_count}")
    print(f"Genesis hash: {GENESIS}")
    print(f"Chain + HMAC verification: {'PASS' if ok else 'FAIL -- TAMPERING OR CORRUPTION DETECTED'}")

    if args.shipment_id:
        rows = conn.execute(
            "SELECT seq, kind, recorded_at FROM audit_log WHERE shipment_id = ? ORDER BY seq",
            (args.shipment_id,),
        ).fetchall()
        print(f"\nTrail for {args.shipment_id} ({len(rows)} entries):")
        for row in rows:
            print(f"  seq={row['seq']:<4} kind={row['kind']:<12} at={row['recorded_at']}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
