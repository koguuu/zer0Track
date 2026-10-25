"""Standalone CLI that writes a signed external checkpoint of the audit
log's current tip -- the anchor half of the tamper-evidence story.

The hash chain alone (scripts/verify_audit_log.py) proves no PAST entry was
edited. It can't by itself prove the log wasn't wholesale truncated and
replaced with a shorter, still-internally-consistent chain -- an attacker
with DB access could delete the tail and the remaining chain still verifies
clean. An anchor closes that gap: a checkpoint of {seq, hash, mac} written
to a separate, out-of-band location (not the mutable database file itself)
that a later verification run can check the live chain against. Wraps
backend.audit.export_checkpoint, which already existed in the codebase but
had nothing calling it.

Usage:
    export ZTRACK_AUDIT_HMAC_KEY="...same key the server used..."
    python3 -m scripts.export_audit_anchor [--db PATH] --anchor PATH

Run this periodically (e.g. a cron job, or after a judging demo) and store
the anchor file somewhere the database's own compromise can't reach --
a different host, write-once storage, or just emailed to a teammate. Then
pass the same --anchor path to scripts/verify_audit_log.py to detect a
truncation/replacement attack, not just a single edited entry.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from backend.audit import export_checkpoint


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
    parser.add_argument("--anchor", required=True,
                         help="Where to write the signed checkpoint. Keep this OUTSIDE the "
                              "database's own storage directory -- the whole point is that "
                              "someone who can edit the DB shouldn't automatically control "
                              "this file too.")
    args = parser.parse_args(argv)

    conn = _connect(args.db)
    try:
        conn.execute("SELECT 1 FROM audit_log LIMIT 1")
    except sqlite3.OperationalError:
        print(f"ERROR: {args.db!r} has no audit_log table -- is this the right database?", file=sys.stderr)
        return 2

    try:
        checkpoint = export_checkpoint(conn, args.anchor)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("(Set ZTRACK_AUDIT_HMAC_KEY to the same key the server used to write this log.)",
              file=sys.stderr)
        return 2

    print(f"Database: {args.db}")
    print(f"Anchor written to: {args.anchor}")
    print(f"Checkpoint: seq={checkpoint['seq']} hash={checkpoint['hash']}")
    print("\nKeep this file outside the database's storage area. Re-run "
          "scripts/verify_audit_log.py with --anchor pointed at it to confirm "
          "the live chain still matches this checkpoint.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
