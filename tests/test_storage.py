from __future__ import annotations

from trividia_truemetrix_daemon.storage import ReadingStore, get_known_device_ids


def _reading(raw: str, value: int = 100) -> dict:
    return dict(
        device_id="Trividia-BLU-11111111",
        model="TRUE METRIX AIR",
        device_time="2026-06-15T14:37:00",
        value_mg_dl=value,
        out_of_range=None,
        is_control_solution=False,
        raw=raw,
        synced_at="2026-06-15T14:40:00+00:00",
    )


def test_record_returns_row_id_for_new_reading(tmp_path):
    store = ReadingStore(str(tmp_path / "readings.db"))
    row_id = store.record(**_reading("65F659D07800"))
    assert row_id is not None
    store.close()


def test_record_dedupes_same_device_and_raw(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    first = store.record(**_reading("65F659D07800"))
    second = store.record(**_reading("65F659D07800"))
    assert first is not None
    assert second is None  # already present -- resyncing the meter is a no-op
    store.close()


def test_record_allows_different_raw_for_same_device(tmp_path):
    store = ReadingStore(str(tmp_path / "readings.db"))
    first = store.record(**_reading("65F659D07800"))
    second = store.record(**_reading("65F659D07801"))
    assert first is not None
    assert second is not None
    store.close()


def test_get_known_device_ids_reflects_stored_readings(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    store.record(**_reading("65F659D07800"))
    store.close()
    assert get_known_device_ids(db_path) == {"Trividia-BLU-11111111"}


def test_get_known_device_ids_empty_for_fresh_db(tmp_path):
    db_path = str(tmp_path / "fresh.db")
    assert get_known_device_ids(db_path) == set()
