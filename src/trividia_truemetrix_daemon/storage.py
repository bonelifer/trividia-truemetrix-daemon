"""SQLite storage backend for synced glucose readings."""

from __future__ import annotations

import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    model TEXT NOT NULL,
    device_time TEXT NOT NULL,
    value_mg_dl INTEGER NOT NULL,
    out_of_range TEXT,
    is_control_solution INTEGER NOT NULL,
    raw TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    UNIQUE(device_id, raw)
);
"""


def ensure_schema(db_path: str) -> None:
    """Create the readings table if missing. Safe to call from any entry point."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(_SCHEMA)
        connection.commit()
    finally:
        connection.close()


def get_known_device_ids(db_path: str) -> set[str]:
    """Return every distinct device_id with at least one stored reading."""
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute("SELECT DISTINCT device_id FROM readings").fetchall()
        return {row[0] for row in rows}
    except sqlite3.OperationalError:
        return set()
    finally:
        connection.close()


class ReadingStore:
    """Persists glucose readings to a local SQLite database, deduplicated.

    The meter's CMD_GET_RESULTS always returns its *entire* on-device
    history, not just readings added since the last sync -- see
    trividia_truemetrix_hid's protocol notes. ``record()`` relies on the
    UNIQUE(device_id, raw) constraint to make re-syncing the same meter
    idempotent: inserting an already-seen (device_id, raw) pair is a no-op
    rather than a duplicate row.
    """

    def __init__(self, db_path: str) -> None:
        ensure_schema(db_path)
        self._connection = sqlite3.connect(db_path)

    def record(
        self,
        device_id: str,
        model: str,
        device_time: str,
        value_mg_dl: int,
        out_of_range: str | None,
        is_control_solution: bool,
        raw: str,
        synced_at: str,
    ) -> int | None:
        """Insert one reading. Returns its row id, or None if already present."""
        cursor = self._connection.execute(
            """
            INSERT OR IGNORE INTO readings (
                device_id, model, device_time, value_mg_dl, out_of_range,
                is_control_solution, raw, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                model,
                device_time,
                value_mg_dl,
                out_of_range,
                int(is_control_solution),
                raw,
                synced_at,
            ),
        )
        self._connection.commit()
        return cursor.lastrowid if cursor.rowcount > 0 else None

    def close(self) -> None:
        self._connection.close()
