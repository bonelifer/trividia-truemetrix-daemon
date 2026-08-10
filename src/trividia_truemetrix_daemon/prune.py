"""Manually delete old readings from the SQLite database."""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone

from ._version import __version__
from .config import ConfigError, load_config


def count_old_rows(db_path: str, cutoff: datetime, device_id: str | None) -> int:
    """Count readings with device_time before cutoff.

    Args:
        db_path: Path to the SQLite database file.
        cutoff: Rows with device_time before this datetime match (naive --
            see delete_old_rows).
        device_id: Restrict to a single meter, if given.

    Returns:
        The number of matching rows.
    """
    query = "SELECT COUNT(*) FROM readings WHERE device_time < ?"
    params: list[str] = [cutoff.isoformat()]
    if device_id:
        query += " AND device_id = ?"
        params.append(device_id)

    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(query, params).fetchone()[0]
    finally:
        connection.close()


def delete_old_rows(db_path: str, cutoff: datetime, device_id: str | None) -> int:
    """Delete readings with device_time before cutoff and reclaim disk space.

    device_time is naive (the meter's own clock, no timezone concept -- see
    storage.py), so cutoff is expected naive too, matching report.py and
    alerting.py's convention elsewhere in this package.

    Args:
        db_path: Path to the SQLite database file.
        cutoff: Rows with device_time before this datetime are deleted.
        device_id: Restrict to a single meter, if given.

    Returns:
        The number of rows deleted.
    """
    query = "DELETE FROM readings WHERE device_time < ?"
    params: list[str] = [cutoff.isoformat()]
    if device_id:
        query += " AND device_id = ?"
        params.append(device_id)

    connection = sqlite3.connect(db_path)
    try:
        deleted = connection.execute(query, params).rowcount
        connection.commit()
        connection.execute("VACUUM")
        return deleted
    finally:
        connection.close()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="trividia-truemetrix-prune",
        description=(
            "Delete readings older than a given number of days. "
            "Dry-run by default -- pass --yes to actually delete."
        ),
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "-c", "--config", help="Path to the daemon's INI config file (reads db_path from it)"
    )
    source.add_argument(
        "-d", "--db", help="Path to the SQLite database file, bypassing the config file"
    )
    parser.add_argument(
        "-o", "--older-than", dest="older_than", type=int, required=True, metavar="DAYS",
        help="Delete readings older than this many days",
    )
    parser.add_argument("-i", "--device-id", help="Restrict pruning to one meter's device_id")
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="Actually delete matching rows (omit for a dry run)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.older_than < 0:
        print("Error: --older-than must be zero or a positive number of days")
        return 1

    db_path = args.db
    if args.config:
        try:
            db_path = load_config(args.config).db_path
        except ConfigError as exc:
            print(f"Error: {exc}")
            return 1

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=args.older_than)

    if not args.yes:
        count = count_old_rows(db_path, cutoff, args.device_id)
        print(
            f"Would delete {count} reading(s) recorded before "
            f"{cutoff.strftime('%Y-%m-%d %H:%M')}. Re-run with --yes to delete."
        )
        return 0

    deleted = delete_old_rows(db_path, cutoff, args.device_id)
    print(f"Deleted {deleted} reading(s) recorded before {cutoff.strftime('%Y-%m-%d %H:%M')}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
