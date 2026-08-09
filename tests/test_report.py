from __future__ import annotations

import csv
import datetime
from dataclasses import replace

from trividia_truemetrix_daemon.assignments import AssignmentStore
from trividia_truemetrix_daemon.config import DEFAULT_REPORT_CONFIG, ProfileConfig, ProfilesConfig
from trividia_truemetrix_daemon.dosing import parse_sliding_scale
from trividia_truemetrix_daemon.report import (
    build_csv,
    build_multi_meter_pdf,
    build_pdf,
    device_ids_for_profile,
    fetch_device_ids,
    fetch_rows,
)
from trividia_truemetrix_daemon.storage import ReadingStore


def _seed(db_path: str) -> None:
    store = ReadingStore(db_path)
    store.record(
        device_id="Trividia-BLU-11111111", model="TRUE METRIX AIR",
        device_time="2026-06-15T08:00:00", value_mg_dl=95, out_of_range=None,
        is_control_solution=False, raw="a", synced_at="2026-06-15T09:00:00+00:00",
    )
    store.record(
        device_id="Trividia-BLU-11111111", model="TRUE METRIX AIR",
        device_time="2026-06-15T18:00:00", value_mg_dl=210, out_of_range=None,
        is_control_solution=False, raw="b", synced_at="2026-06-15T19:00:00+00:00",
    )
    store.record(
        device_id="Trividia-MR2-22222222", model="TRUE METRIX",
        device_time="2026-06-16T08:00:00", value_mg_dl=601, out_of_range="high",
        is_control_solution=False, raw="c", synced_at="2026-06-16T09:00:00+00:00",
    )
    store.close()


def _profiles() -> ProfilesConfig:
    return ProfilesConfig(
        profiles={
            "Alice": ProfileConfig(
                full_name="Alice Smith", email="", notes="", sliding_scale=(),
                device_ids=("Trividia-BLU-11111111",),
                high_threshold_mg_dl=None, low_threshold_mg_dl=None,
            )
        }
    )


def test_fetch_rows_returns_all_by_default(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))

    rows = fetch_rows(db_path, _profiles(), assignments, None, None, None)
    assert len(rows) == 3
    assert rows[0].device_time < rows[-1].device_time  # oldest first


def test_fetch_rows_filters_by_device_id(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))

    rows = fetch_rows(
        db_path, _profiles(), assignments, "Trividia-MR2-22222222", None, None
    )
    assert len(rows) == 1
    assert rows[0].out_of_range == "high"


def test_fetch_rows_filters_by_profile(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))

    rows = fetch_rows(db_path, _profiles(), assignments, None, None, None, profile="Alice")
    assert len(rows) == 2
    assert all(row.device_id == "Trividia-BLU-11111111" for row in rows)
    assert all(row.profile == "Alice" for row in rows)


def test_fetch_rows_attaches_profile_from_dynamic_assignment(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    assignments.set("Trividia-MR2-22222222", "Bob")

    rows = fetch_rows(db_path, _profiles(), assignments, "Trividia-MR2-22222222", None, None)
    assert rows[0].profile == "Bob"


def test_fetch_rows_unassigned_device_has_no_profile(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))

    rows = fetch_rows(db_path, _profiles(), assignments, "Trividia-MR2-22222222", None, None)
    assert rows[0].profile is None


def test_fetch_rows_respects_date_range(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))

    rows = fetch_rows(
        db_path, _profiles(), assignments, None,
        datetime.datetime(2026, 6, 16), None,
    )
    assert len(rows) == 1
    assert rows[0].device_id == "Trividia-MR2-22222222"


def test_fetch_device_ids(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    assert fetch_device_ids(db_path, None, None) == [
        "Trividia-BLU-11111111",
        "Trividia-MR2-22222222",
    ]


def test_device_ids_for_profile_combines_static_and_dynamic(tmp_path):
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    assignments.set("Trividia-MR2-99999999", "Alice")

    ids = device_ids_for_profile("Alice", _profiles(), assignments)
    assert ids == ["Trividia-BLU-11111111", "Trividia-MR2-99999999"]


def test_build_csv_writes_expected_columns(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    rows = fetch_rows(db_path, _profiles(), assignments, None, None, None)

    out_path = str(tmp_path / "report.csv")
    build_csv(rows, out_path)

    with open(out_path, newline="") as fp:
        reader = list(csv.reader(fp))
    assert reader[0] == ["Date/Time", "Device ID", "Model", "Glucose (mg/dL)", "Flag"]
    assert len(reader) == 4  # header + 3 rows
    assert reader[3][-1] == "HIGH"


def test_build_pdf_produces_nonempty_file(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    rows = fetch_rows(db_path, _profiles(), assignments, None, None, None)

    out_path = str(tmp_path / "report.pdf")
    build_pdf(rows, out_path)
    assert tmp_path.joinpath("report.pdf").stat().st_size > 0


def test_build_multi_meter_pdf_produces_nonempty_file(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    device_ids = fetch_device_ids(db_path, None, None)
    sections = [
        (d, fetch_rows(db_path, _profiles(), assignments, d, None, None)) for d in device_ids
    ]

    out_path = str(tmp_path / "multi.pdf")
    build_multi_meter_pdf(sections, out_path)
    assert tmp_path.joinpath("multi.pdf").stat().st_size > 0


def _sliding_scale_report_config(**overrides):
    return replace(DEFAULT_REPORT_CONFIG, include_sliding_scale=True, **overrides)


def test_build_csv_includes_dose_and_note_columns(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    rows = fetch_rows(
        db_path, _profiles(), assignments, "Trividia-BLU-11111111", None, None
    )
    bands = parse_sliding_scale(
        "0:100:0:in range\n101:200:2\n201::4:call doctor", "test"
    )

    out_path = str(tmp_path / "report.csv")
    build_csv(rows, out_path, _sliding_scale_report_config(), bands)

    with open(out_path, newline="") as fp:
        reader = list(csv.reader(fp))
    assert reader[0][-2:] == ["Dose (units)", "Note"]
    # First seeded Alice reading is 95 mg/dL -> in the 0-100 band, dose 0.
    assert reader[1][-2:] == ["0", "in range"]
    # Second seeded Alice reading is 210 mg/dL -> the 201+ band, dose 4.
    assert reader[2][-2:] == ["4", "call doctor"]


def test_build_csv_shows_no_guidance_for_uncovered_gap(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    rows = fetch_rows(
        db_path, _profiles(), assignments, "Trividia-BLU-11111111", None, None
    )
    # 95 and 210 mg/dL both fall in the gap between these two bands.
    bands = parse_sliding_scale("0:50:0\n300::4", "test")

    out_path = str(tmp_path / "report.csv")
    build_csv(rows, out_path, _sliding_scale_report_config(), bands)

    with open(out_path, newline="") as fp:
        reader = list(csv.reader(fp))
    assert reader[1][-2:] == ["", "no guidance configured"]


def test_build_csv_omits_dose_columns_when_disabled(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    rows = fetch_rows(
        db_path, _profiles(), assignments, "Trividia-BLU-11111111", None, None
    )
    bands = parse_sliding_scale("0:500:0", "test")

    out_path = str(tmp_path / "report.csv")
    build_csv(rows, out_path, DEFAULT_REPORT_CONFIG, bands)  # include_sliding_scale=False

    with open(out_path, newline="") as fp:
        header = next(csv.reader(fp))
    assert "Dose (units)" not in header


def test_build_pdf_with_sliding_scale_produces_nonempty_file(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    rows = fetch_rows(
        db_path, _profiles(), assignments, "Trividia-BLU-11111111", None, None
    )
    bands = parse_sliding_scale("0:500:2", "test")

    out_path = str(tmp_path / "report.pdf")
    build_pdf(rows, out_path, _sliding_scale_report_config(), bands)
    assert tmp_path.joinpath("report.pdf").stat().st_size > 0


def test_build_multi_meter_pdf_resolves_sliding_scale_per_section(tmp_path):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    profiles = ProfilesConfig(
        profiles={
            "Alice": ProfileConfig(
                full_name="Alice", email="", notes="",
                device_ids=("Trividia-BLU-11111111",),
                sliding_scale=parse_sliding_scale("0:500:2", "test"),
                high_threshold_mg_dl=None, low_threshold_mg_dl=None,
            )
        }
    )
    device_ids = fetch_device_ids(db_path, None, None)
    sections = [
        (d, fetch_rows(db_path, profiles, assignments, d, None, None)) for d in device_ids
    ]

    out_path = str(tmp_path / "multi.pdf")
    build_multi_meter_pdf(sections, out_path, _sliding_scale_report_config(), profiles)
    assert tmp_path.joinpath("multi.pdf").stat().st_size > 0
