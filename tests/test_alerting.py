from __future__ import annotations

import datetime

from trividia_truemetrix_daemon.alerting import check_alerts
from trividia_truemetrix_daemon.assignments import AssignmentStore
from trividia_truemetrix_daemon.config import AlertConfig, ProfileConfig, ProfilesConfig
from trividia_truemetrix_daemon.storage import ReadingStore

_NOW = datetime.datetime(2026, 6, 20, 12, 0, 0)


def _record(store, device_id, device_time, value_mg_dl, raw):
    store.record(
        device_id=device_id, model="TRUE METRIX AIR", device_time=device_time,
        value_mg_dl=value_mg_dl, out_of_range=None, is_control_solution=False,
        raw=raw, synced_at=device_time + "+00:00",
    )


def _alert_config(**overrides):
    base = dict(
        enabled=True, apprise_urls=["mailto://x"], high_threshold_mg_dl=0,
        low_threshold_mg_dl=0, stale_after_days=0, state_path="/dev/null/unused",
    )
    base.update(overrides)
    return AlertConfig(**base)


def _empty_profiles():
    return ProfilesConfig(profiles={})


def test_check_alerts_no_readings_produces_nothing(tmp_path):
    db_path = str(tmp_path / "readings.db")
    ReadingStore(db_path).close()
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    alert_config = _alert_config(high_threshold_mg_dl=200, state_path=str(tmp_path / "s.json"))

    messages = check_alerts(db_path, alert_config, _empty_profiles(), assignments, now=_NOW)
    assert messages == []


def test_check_alerts_fires_high_threshold(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "dev-1", "2026-06-20T08:00:00", 250, "r1")
    store.close()
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    alert_config = _alert_config(high_threshold_mg_dl=200, state_path=str(tmp_path / "s.json"))

    messages = check_alerts(db_path, alert_config, _empty_profiles(), assignments, now=_NOW)
    assert len(messages) == 1
    assert "250 mg/dL" in messages[0]
    assert "above" in messages[0]


def test_check_alerts_fires_low_threshold(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "dev-1", "2026-06-20T08:00:00", 60, "r1")
    store.close()
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    alert_config = _alert_config(low_threshold_mg_dl=70, state_path=str(tmp_path / "s.json"))

    messages = check_alerts(db_path, alert_config, _empty_profiles(), assignments, now=_NOW)
    assert len(messages) == 1
    assert "60 mg/dL" in messages[0]
    assert "below" in messages[0]


def test_check_alerts_within_threshold_produces_nothing(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "dev-1", "2026-06-20T08:00:00", 120, "r1")
    store.close()
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    alert_config = _alert_config(
        high_threshold_mg_dl=200, low_threshold_mg_dl=70, state_path=str(tmp_path / "s.json")
    )

    messages = check_alerts(db_path, alert_config, _empty_profiles(), assignments, now=_NOW)
    assert messages == []


def test_check_alerts_does_not_repeat_for_same_reading(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "dev-1", "2026-06-20T08:00:00", 250, "r1")
    store.close()
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    alert_config = _alert_config(high_threshold_mg_dl=200, state_path=str(tmp_path / "s.json"))

    first = check_alerts(db_path, alert_config, _empty_profiles(), assignments, now=_NOW)
    second = check_alerts(db_path, alert_config, _empty_profiles(), assignments, now=_NOW)
    assert len(first) == 1
    assert second == []


def test_check_alerts_fires_again_for_new_high_reading(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "dev-1", "2026-06-20T08:00:00", 250, "r1")
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    alert_config = _alert_config(high_threshold_mg_dl=200, state_path=str(tmp_path / "s.json"))
    check_alerts(db_path, alert_config, _empty_profiles(), assignments, now=_NOW)

    _record(store, "dev-1", "2026-06-20T14:00:00", 260, "r2")
    store.close()
    messages = check_alerts(db_path, alert_config, _empty_profiles(), assignments, now=_NOW)
    assert len(messages) == 1
    assert "260 mg/dL" in messages[0]


def test_check_alerts_fires_staleness(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "dev-1", "2026-06-01T08:00:00", 120, "r1")
    store.close()
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    alert_config = _alert_config(stale_after_days=2, state_path=str(tmp_path / "s.json"))

    messages = check_alerts(db_path, alert_config, _empty_profiles(), assignments, now=_NOW)
    assert len(messages) == 1
    assert "dev-1" in messages[0]
    assert "2 day" in messages[0]


def test_check_alerts_staleness_throttled_within_a_day(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "dev-1", "2026-06-01T08:00:00", 120, "r1")
    store.close()
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    alert_config = _alert_config(stale_after_days=2, state_path=str(tmp_path / "s.json"))

    first = check_alerts(db_path, alert_config, _empty_profiles(), assignments, now=_NOW)
    second = check_alerts(
        db_path, alert_config, _empty_profiles(), assignments,
        now=_NOW + datetime.timedelta(hours=1),
    )
    assert len(first) == 1
    assert second == []


def test_check_alerts_staleness_repeats_after_throttle_window(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "dev-1", "2026-06-01T08:00:00", 120, "r1")
    store.close()
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    alert_config = _alert_config(stale_after_days=2, state_path=str(tmp_path / "s.json"))

    check_alerts(db_path, alert_config, _empty_profiles(), assignments, now=_NOW)
    later = check_alerts(
        db_path, alert_config, _empty_profiles(), assignments,
        now=_NOW + datetime.timedelta(days=2),
    )
    assert len(later) == 1


def test_check_alerts_staleness_clears_once_new_reading_arrives(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "dev-1", "2026-06-01T08:00:00", 120, "r1")
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    alert_config = _alert_config(stale_after_days=2, state_path=str(tmp_path / "s.json"))
    check_alerts(db_path, alert_config, _empty_profiles(), assignments, now=_NOW)

    _record(store, "dev-1", "2026-06-20T09:00:00", 120, "r2")
    store.close()
    messages = check_alerts(
        db_path, alert_config, _empty_profiles(), assignments,
        now=_NOW + datetime.timedelta(hours=2),
    )
    assert messages == []


def test_check_alerts_uses_profile_threshold_override(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "dev-1", "2026-06-20T08:00:00", 220, "r1")
    store.close()
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    # Global default is 300 (would not fire), but Alice's own override is 200.
    profiles = ProfilesConfig(
        profiles={
            "Alice": ProfileConfig(
                full_name="Alice", email="", notes="", device_ids=("dev-1",),
                sliding_scale=(), high_threshold_mg_dl=200, low_threshold_mg_dl=None,
                tir_low_mg_dl=None, tir_high_mg_dl=None,
            )
        }
    )
    alert_config = _alert_config(high_threshold_mg_dl=300, state_path=str(tmp_path / "s.json"))

    messages = check_alerts(db_path, alert_config, profiles, assignments, now=_NOW)
    assert len(messages) == 1
    assert "220 mg/dL" in messages[0]


def test_check_alerts_profile_override_can_disable_a_global_threshold(tmp_path):
    db_path = str(tmp_path / "readings.db")
    store = ReadingStore(db_path)
    _record(store, "dev-1", "2026-06-20T08:00:00", 220, "r1")
    store.close()
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    profiles = ProfilesConfig(
        profiles={
            "Alice": ProfileConfig(
                full_name="Alice", email="", notes="", device_ids=("dev-1",),
                sliding_scale=(), high_threshold_mg_dl=0, low_threshold_mg_dl=None,
                tir_low_mg_dl=None, tir_high_mg_dl=None,
            )
        }
    )
    alert_config = _alert_config(high_threshold_mg_dl=200, state_path=str(tmp_path / "s.json"))

    messages = check_alerts(db_path, alert_config, profiles, assignments, now=_NOW)
    assert messages == []
