"""Safe read-only demonstration: mutate an isolated database copy, never the source."""
import argparse
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from backend.audit import verify_audit_chain


def demonstrate(db_path: str) -> tuple[bool, bool]:
    source = Path(db_path)

    if not source.is_file():
        raise FileNotFoundError(source)

    with tempfile.TemporaryDirectory(prefix="zer0track-audit-demo-") as folder:
        dest = Path(folder) / "sandbox.sqlite"

        with (
            closing(sqlite3.connect(source)) as original,
            closing(sqlite3.connect(dest)) as copy,
        ):
            original.backup(copy)

        with closing(sqlite3.connect(dest)) as conn:
            conn.row_factory = sqlite3.Row
            initial = verify_audit_chain(conn)

            row = conn.execute(
                "SELECT seq FROM audit_log ORDER BY seq LIMIT 1"
            ).fetchone()

            if row is None:
                raise ValueError("No audit events to demonstrate tampering")

            conn.execute(
                "UPDATE audit_log SET payload=? WHERE seq=?",
                ('{"synthetic_tamper_demo":true}', row["seq"]),
            )
            conn.commit()
            after = verify_audit_chain(conn)

    return initial, after


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.getenv("ZTRACK_DB_PATH", "./var/zer0track.sqlite"))
    args = parser.parse_args()
    before, after = demonstrate(args.db)
    print(f"Original snapshot valid: {before}")
    print(f"Tampered sandbox valid: {after}")
    if not before or after:
        raise SystemExit("Audit demonstration did not behave as expected")
    print("PASS: changes to the sandbox audit payload were detected; original DB untouched")
