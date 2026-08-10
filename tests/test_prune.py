from __future__ import annotations

import datetime

from trividia_truemetrix_daemon.prune import count_old_rows, delete_old_rows
from trividia_truemetrix_daemon.storage import ReadingStore


def _seed(db_path: str) -> None:
    store = ReadingStore(db_path)
    store.record(
        device_id="dev-1", model="TRUE METRIX AIR", device_time="2026-01-01T08:00:00",
        value_mg_dl=100, out_of_range=None, is_control_solution=False, raw="old",
        synced_at="2026-01-01T09:00:00+00:00",
    )
    store.record(
        device_id="dev-1", model="TRUE METRIX AIR", device_time="2026-06-20T08:00:00",
        value_mg_dl=110, out_of_range=None, is_control_solution=False, raw="new",
        synced_at="2026-06-20T09:00:00+00:00",
    )
    store.record(
        device_id="dev-2", model="TRUE METRIX", device_time="2026-01-01T08:00:00",
        value_mg_dl=120, out_of_range=None, is_control_solution=False, raw="old2",
        synced_at="2026-01-01T09:00:00+00:00",
    )
    store.close()


def test_count_old_rows_counts_before_cutoff(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    cutoff = datetime.datetime(2026, 3, 1)
    assert count_old_rows(db_path, cutoff, None) == 2


def test_count_old_rows_filters_by_device_id(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    cutoff = datetime.datetime(2026, 3, 1)
    assert count_old_rows(db_path, cutoff, "dev-1") == 1


def test_delete_old_rows_removes_matching_and_keeps_rest(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    cutoff = datetime.datetime(2026, 3, 1)

    deleted = delete_old_rows(db_path, cutoff, None)
    assert deleted == 2
    assert count_old_rows(db_path, datetime.datetime(2027, 1, 1), None) == 1


def test_delete_old_rows_scoped_to_device_id(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    cutoff = datetime.datetime(2026, 3, 1)

    deleted = delete_old_rows(db_path, cutoff, "dev-2")
    assert deleted == 1
    # dev-1's old row is untouched.
    assert count_old_rows(db_path, cutoff, "dev-1") == 1


def test_delete_old_rows_nothing_matches(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    cutoff = datetime.datetime(2020, 1, 1)
    assert delete_old_rows(db_path, cutoff, None) == 0
