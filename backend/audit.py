"""Tamper-evident hash-linked audit log with HMAC and optional external anchor.

The signing key and anchor must be protected separately from the SQLite DB.
No claim is made that a hash chain alone prevents tampering.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from backend.models import ApprovalRecord

GENESIS = "0" * 64


def _secret() -> bytes:
    key = os.environ.get("ZTRACK_AUDIT_HMAC_KEY", "")
    if len(key) < 32:
        raise RuntimeError("ZTRACK_AUDIT_HMAC_KEY must contain at least 32 characters")
    return key.encode("utf-8")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def setup_audit(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS audit_log (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, shipment_id TEXT NOT NULL,
        kind TEXT NOT NULL, recorded_at TEXT NOT NULL, payload TEXT NOT NULL,
        prev_hash TEXT NOT NULL, entry_hash TEXT NOT NULL, mac TEXT NOT NULL
    )""")


def append_audit(conn: sqlite3.Connection, shipment_id: str, kind: str, payload: object) -> int:
    key = _secret()
    row = conn.execute("SELECT seq, entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    prev = row["entry_hash"] if row is not None else GENESIS
    when = datetime.now(timezone.utc).isoformat()
    payload_text = _canonical(payload)
    msg = _canonical({"shipment_id": shipment_id, "kind": kind, "recorded_at": when,
                      "payload": payload_text, "prev_hash": prev})
    digest = hashlib.sha256(msg.encode()).hexdigest()
    mac = hmac.new(key, digest.encode(), hashlib.sha256).hexdigest()
    cur = conn.execute("INSERT INTO audit_log(shipment_id, kind, recorded_at, payload, prev_hash, entry_hash, mac) "
                       "VALUES(?,?,?,?,?,?,?)", (shipment_id, kind, when, payload_text, prev, digest, mac))
    return int(cur.lastrowid)


def log_approval(record: ApprovalRecord, conn: sqlite3.Connection) -> int:
    return append_audit(conn, record.shipment_id, "APPROVAL", record.model_dump(mode="json"))


def get_audit_trail(shipment_id: str, conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM audit_log WHERE shipment_id = ? ORDER BY seq", (shipment_id,)).fetchall()
    return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]


def verify_audit_chain(conn: sqlite3.Connection, anchor_path: str | None = None) -> bool:
    key = _secret()
    previous = GENESIS
    last_seq = 0
    for row in conn.execute("SELECT * FROM audit_log ORDER BY seq"):
        if row["prev_hash"] != previous or row["seq"] != last_seq + 1:
            return False
        msg = _canonical({"shipment_id": row["shipment_id"], "kind": row["kind"],
                          "recorded_at": row["recorded_at"], "payload": row["payload"],
                          "prev_hash": row["prev_hash"]})
        digest = hashlib.sha256(msg.encode()).hexdigest()
        mac = hmac.new(key, digest.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest, row["entry_hash"]) or not hmac.compare_digest(mac, row["mac"]):
            return False
        previous = digest
        last_seq = row["seq"]
    if anchor_path:
        if not Path(anchor_path).is_file():
            return False  # a configured independent anchor must exist
        try:
            data = json.loads(Path(anchor_path).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("seq"), int) or data["seq"] < 0:
                return False
            if not isinstance(data.get("hash"), str) or not isinstance(data.get("mac"), str):
                return False
        except (OSError, ValueError, TypeError):
            return False
        anchor_mac = hmac.new(key, f"{data['seq']}:{data['hash']}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(anchor_mac, data["mac"]):
            return False
        # The checkpoint must match the chain exactly at its stored sequence.
        if data["seq"] > last_seq:
            return False
        anchored = (conn.execute("SELECT entry_hash FROM audit_log WHERE seq=?", (data["seq"],)).fetchone()
                    if data["seq"] > 0 else None)
        expected = anchored["entry_hash"] if anchored else GENESIS
        if expected != data["hash"]:
            return False
    return True


def export_checkpoint(conn: sqlite3.Connection, path: str) -> dict:
    """Write a signed checkpoint. Store path outside mutable database/storage area."""
    row = conn.execute("SELECT seq, entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
    seq, digest = (row["seq"], row["entry_hash"]) if row else (0, GENESIS)
    mac = hmac.new(_secret(), f"{seq}:{digest}".encode(), hashlib.sha256).hexdigest()
    content = {"seq": seq, "hash": digest, "mac": mac}
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(_canonical(content), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, output)
    return content
