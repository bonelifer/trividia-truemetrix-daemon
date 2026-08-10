#!/usr/bin/env python3
"""Create a tiny fixture SQLite database for smoke/CI testing."""

import sqlite3
import sys
from datetime import datetime, timezone


def main() -> None:
    db_path = sys.argv[1]
    con = sqlite3.connect(db_path)
    con.execute(
        """
        CREATE TABLE readings (
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
        )
        """
    )
    con.execute(
        "INSERT INTO readings "
        "(device_id, model, device_time, value_mg_dl, out_of_range, "
        "is_control_solution, raw, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "Trividia-BLU-12345678",
            "TRUE METRIX AIR",
            "2026-06-15T08:00:00",
            110,
            None,
            0,
            "fixture-1",
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    con.commit()
    con.close()


if __name__ == "__main__":
    main()
