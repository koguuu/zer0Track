"""Export/verify signed audit checkpoints to an independently retained destination."""
import argparse
import os
import sqlite3
from pathlib import Path
from backend.audit import export_checkpoint, verify_audit_chain


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["export", "verify"])
    parser.add_argument("--db", default=os.getenv("ZTRACK_DB_PATH", "./var/zer0track.sqlite"))
    parser.add_argument("--anchor", default=os.getenv("ZTRACK_AUDIT_ANCHOR_PATH"))
    args = parser.parse_args()
    if not args.anchor:
        parser.error("--anchor is required; choose a path OUTSIDE the mutable DB location")
    db = Path(args.db).resolve()
    anchor = Path(args.anchor).resolve()
    if anchor == db or anchor.parent == db.parent:
        parser.error("Keep the anchor outside the database directory")
    if args.command == "verify" and not anchor.is_file():
        raise SystemExit("FAIL: anchor missing; cannot claim independently verified audit")
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        if args.command == "export":
            if not verify_audit_chain(conn, str(anchor) if anchor.exists() else None):
                raise SystemExit("FAIL: chain/previous anchor invalid; refusing export")
            print("Anchor exported:", export_checkpoint(conn, str(anchor)))
        else:
            if not verify_audit_chain(conn, str(anchor)):
                raise SystemExit("FAIL: chain or signed anchor mismatch")
            print("PASS: chain matches independently retained signed checkpoint")


if __name__ == "__main__":
    main()
