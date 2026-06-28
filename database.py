"""
Database layer — SQLite via the built-in sqlite3 module.
All public functions are thread-safe (check_same_thread=False + explicit
connection-per-call pattern keeps things simple for a single-process bot).
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime
from typing import Optional

import config

# ── Order status constants ────────────────────────────────────────────────────
STATUS_PENDING   = "pending"    # waiting for admin review
STATUS_APPROVED  = "approved"   # rank granted
STATUS_REJECTED  = "rejected"   # admin rejected


# ── Internal helpers ──────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DATABASE_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# ── Schema ────────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create tables if they don't exist yet. Call once at startup."""
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id          TEXT PRIMARY KEY,
                user_id     INTEGER NOT NULL,
                username    TEXT,
                ign         TEXT NOT NULL,
                rank_key    TEXT NOT NULL,
                rank_label  TEXT NOT NULL,
                duration    TEXT NOT NULL,
                months      INTEGER NOT NULL,
                price       REAL NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',
                receipt_file_id TEXT,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)
        # Per-user conversation state (simple key-value)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_state (
                user_id     INTEGER PRIMARY KEY,
                state       TEXT NOT NULL DEFAULT 'idle',
                data        TEXT          -- JSON blob for in-progress order draft
            )
        """)
        conn.commit()


# ── Orders ────────────────────────────────────────────────────────────────────

def create_order(
    *,
    user_id: int,
    username: Optional[str],
    ign: str,
    rank_key: str,
    rank_label: str,
    duration: str,
    months: int,
    price: float,
    receipt_file_id: str,
) -> str:
    """Insert a new order and return its ID."""
    order_id = str(uuid.uuid4())[:8].upper()
    now = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO orders
                (id, user_id, username, ign, rank_key, rank_label,
                 duration, months, price, status, receipt_file_id,
                 created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                order_id, user_id, username, ign, rank_key, rank_label,
                duration, months, price, STATUS_PENDING, receipt_file_id,
                now, now,
            ),
        )
        conn.commit()
    return order_id


def get_order(order_id: str) -> Optional[sqlite3.Row]:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone()


def update_order_status(order_id: str, status: str) -> None:
    now = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, order_id),
        )
        conn.commit()


def has_pending_order(user_id: int) -> bool:
    """Return True if the user already has an unresolved order."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM orders WHERE user_id = ? AND status = ?",
            (user_id, STATUS_PENDING),
        ).fetchone()
    return row is not None


# ── User state (conversation FSM) ─────────────────────────────────────────────

def get_user_state(user_id: int) -> tuple[str, Optional[str]]:
    """Return (state, data_json) for the user. Defaults to ('idle', None)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT state, data FROM user_state WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    if row:
        return row["state"], row["data"]
    return "idle", None


def set_user_state(user_id: int, state: str, data: Optional[str] = None) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_state (user_id, state, data)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET state = excluded.state,
                                               data  = excluded.data
            """,
            (user_id, state, data),
        )
        conn.commit()


def clear_user_state(user_id: int) -> None:
    set_user_state(user_id, "idle", None)
