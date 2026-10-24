"""Staff credential verification for zer0Track's synthetic demo."""

import hmac
import json
import os
from datetime import datetime, timedelta, timezone


MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def verify_staff(store, operator_id: str, token: str) -> str:
    """Return 'ok', 'invalid', or 'locked'."""
    now = datetime.now(timezone.utc)
    operator_id = operator_id.strip()

    if not operator_id or not token:
        return "invalid"

    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")

        row = conn.execute(
            "SELECT failed_attempts, locked_until "
            "FROM staff_login_attempts WHERE operator_id=?",
            (operator_id,),
        ).fetchone()

        if row and row["locked_until"]:
            locked_until = datetime.fromisoformat(row["locked_until"])
            if now < locked_until:
                return "locked"

        try:
            credentials = json.loads(
                os.environ.get("ZTRACK_SUPERVISOR_TOKENS", "{}")
            )
        except (ValueError, TypeError):
            credentials = {}

        expected = (
            credentials.get(operator_id)
            if isinstance(credentials, dict)
            else None
        )

        valid = (
            isinstance(expected, str)
            and bool(expected)
            and hmac.compare_digest(expected, token)
        )

        if valid:
            conn.execute(
                "DELETE FROM staff_login_attempts WHERE operator_id=?",
                (operator_id,),
            )
            return "ok"

        attempts = (row["failed_attempts"] if row else 0) + 1
        if row and row["locked_until"]:
            attempts = 1

        lockout = (
            (now + timedelta(minutes=LOCKOUT_MINUTES)).isoformat()
            if attempts >= MAX_ATTEMPTS
            else None
        )

        conn.execute(
            "INSERT INTO staff_login_attempts "
            "(operator_id, failed_attempts, locked_until, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(operator_id) DO UPDATE SET "
            "failed_attempts=excluded.failed_attempts, "
            "locked_until=excluded.locked_until, "
            "updated_at=excluded.updated_at",
            (operator_id, attempts, lockout, now.isoformat()),
        )

        return "locked" if lockout else "invalid"


# Staff session management
import hashlib
import secrets

SESSION_MINUTES = 30


def create_staff_session(store, operator_id: str) -> dict:
    """Issue a random token and store only its SHA-256 hash."""
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=SESSION_MINUTES
    )

    with store.connect() as conn:
        conn.execute(
            "INSERT INTO staff_sessions "
            "(token_hash, operator_id, expires_at) VALUES (?, ?, ?)",
            (token_hash, operator_id, expires_at.isoformat()),
        )

    return {
        "session_token": token,
        "operator_id": operator_id,
        "expires_at": expires_at.isoformat(),
    }


def verify_staff_session(store, token: str) -> str | None:
    """Return operator ID for an active session, otherwise None."""
    if not token:
        return None

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    now = datetime.now(timezone.utc)

    with store.connect() as conn:
        row = conn.execute(
            "SELECT operator_id, expires_at FROM staff_sessions "
            "WHERE token_hash=?",
            (token_hash,),
        ).fetchone()

        if row is None:
            return None

        expiry = datetime.fromisoformat(row["expires_at"])

        if now >= expiry:
            conn.execute(
                "DELETE FROM staff_sessions WHERE token_hash=?",
                (token_hash,),
            )
            return None

        return row["operator_id"]


def revoke_staff_session(store, token: str) -> bool:
    """Revoke one staff session without exposing its raw token."""
    if not token:
        return False

    token_hash = hashlib.sha256(token.encode()).hexdigest()

    with store.connect() as conn:
        cursor = conn.execute(
            "DELETE FROM staff_sessions WHERE token_hash=?",
            (token_hash,),
        )
        return cursor.rowcount > 0
