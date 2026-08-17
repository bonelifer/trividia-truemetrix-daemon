from __future__ import annotations

from dataclasses import replace

from aiohttp.test_utils import TestClient, TestServer

from trividia_truemetrix_daemon.api import build_app
from trividia_truemetrix_daemon.assignments import AssignmentStore
from trividia_truemetrix_daemon.config import (
    DEFAULT_REPORT_CONFIG,
    ApiConfig,
    MqttConfig,
    ProfileConfig,
    ProfilesConfig,
)
from trividia_truemetrix_daemon.dosing import parse_sliding_scale
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
        device_time="2026-06-16T08:00:00", value_mg_dl=210, out_of_range=None,
        is_control_solution=False, raw="b", synced_at="2026-06-16T09:00:00+00:00",
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
                full_name="Alice Smith", email="", notes="",
                device_ids=("Trividia-BLU-11111111",),
                sliding_scale=parse_sliding_scale("0:500:2", "test"),
                high_threshold_mg_dl=None, low_threshold_mg_dl=None,
                tir_low_mg_dl=None, tir_high_mg_dl=None,
            )
        }
    )


async def _client(tmp_path, token="", report_config=DEFAULT_REPORT_CONFIG, mqtt_config=None):
    db_path = str(tmp_path / "readings.db")
    _seed(db_path)
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    app = build_app(
        db_path, assignments, ApiConfig(enabled=True, host="x", port=0, token=token),
        _profiles(), report_config, mqtt_config,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_health_is_unauthenticated_even_with_token(tmp_path):
    client = await _client(tmp_path, token="secret")
    try:
        resp = await client.get("/api/v1/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"
    finally:
        await client.close()


async def test_capabilities_is_unauthenticated_and_reports_mqtt_disabled(tmp_path):
    client = await _client(tmp_path, token="secret")
    try:
        resp = await client.get("/api/v1/capabilities")
        assert resp.status == 200
        data = await resp.json()
        assert data["daemon"] == "trividia-truemetrix"
        assert data["api_version"] == "v1"
        assert data["measurement_types"] == ["glucose"]
        assert data["measurement_modes"] == ["spot"]
        assert data["profile_model"] == "assignable"
        assert "device_time" in data["timestamp_fields"]
        assert "synced_at" in data["timestamp_fields"]
        assert data["mqtt"] == {"enabled": False}
    finally:
        await client.close()


async def test_capabilities_reports_mqtt_enabled_with_topic_pattern(tmp_path):
    mqtt_config = MqttConfig(
        enabled=True, host="broker", port=1883, username="", password="",
        use_tls=False, topic_prefix="trividia_truemetrix_daemon", qos=0, retain=True,
    )
    client = await _client(tmp_path, mqtt_config=mqtt_config)
    try:
        resp = await client.get("/api/v1/capabilities")
        assert resp.status == 200
        data = await resp.json()
        assert data["mqtt"] == {
            "enabled": True,
            "topic_pattern": "trividia_truemetrix_daemon/<device_id>/state",
        }
    finally:
        await client.close()


async def test_latest_returns_most_recent_reading_per_meter(tmp_path):
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/v1/latest")
        assert resp.status == 200
        data = await resp.json()
        by_device = {row["device_id"]: row for row in data}
        assert by_device["Trividia-BLU-11111111"]["value_mg_dl"] == 210
        assert by_device["Trividia-BLU-11111111"]["profile"] == "Alice"
        assert by_device["Trividia-MR2-22222222"]["profile"] is None
    finally:
        await client.close()


async def test_latest_filters_by_device_id(tmp_path):
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/v1/latest", params={"device_id": "Trividia-MR2-22222222"})
        data = await resp.json()
        assert len(data) == 1
        assert data[0]["value_mg_dl"] == 601
    finally:
        await client.close()


async def test_latest_filters_by_profile(tmp_path):
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/v1/latest", params={"profile": "Alice"})
        data = await resp.json()
        assert len(data) == 1
        assert data[0]["device_id"] == "Trividia-BLU-11111111"
    finally:
        await client.close()


async def test_latest_404_when_no_match(tmp_path):
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/v1/latest", params={"device_id": "nonexistent"})
        assert resp.status == 404
    finally:
        await client.close()


async def test_requires_auth_when_token_configured(tmp_path):
    client = await _client(tmp_path, token="secret")
    try:
        resp = await client.get("/api/v1/latest")
        assert resp.status == 401
        resp = await client.get("/api/v1/latest", headers={"Authorization": "Bearer secret"})
        assert resp.status == 200
    finally:
        await client.close()


async def test_assign_device_rejects_unknown_profile(tmp_path):
    client = await _client(tmp_path)
    try:
        resp = await client.get(
            "/api/v1/assign-device", params={"device_id": "dev-x", "profile": "Nobody"}
        )
        assert resp.status == 400
    finally:
        await client.close()


async def test_assign_device_accepts_get_and_post(tmp_path):
    client = await _client(tmp_path)
    try:
        resp = await client.get(
            "/api/v1/assign-device", params={"device_id": "dev-x", "profile": "Alice"}
        )
        assert resp.status == 200
        resp = await client.post(
            "/api/v1/assign-device", params={"device_id": "dev-y", "profile": "Alice"}
        )
        assert resp.status == 200
    finally:
        await client.close()


async def test_report_pdf_download(tmp_path):
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/v1/report", params={"device_id": "Trividia-BLU-11111111"})
        assert resp.status == 200
        assert resp.content_type == "application/pdf"
        body = await resp.read()
        assert len(body) > 0
    finally:
        await client.close()


async def test_report_csv_download(tmp_path):
    client = await _client(tmp_path)
    try:
        resp = await client.get(
            "/api/v1/report", params={"device_id": "Trividia-BLU-11111111", "format": "csv"}
        )
        assert resp.status == 200
        assert resp.content_type == "text/csv"
        body = (await resp.read()).decode()
        assert "Glucose (mg/dL)" in body
        assert "95.00" in body
    finally:
        await client.close()


async def test_report_multi_meter(tmp_path):
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/v1/report", params={"multi_meter": "1"})
        assert resp.status == 200
        assert resp.content_type == "application/pdf"
    finally:
        await client.close()


async def test_report_rejects_multi_meter_with_device_id(tmp_path):
    client = await _client(tmp_path)
    try:
        resp = await client.get(
            "/api/v1/report", params={"multi_meter": "1", "device_id": "Trividia-BLU-11111111"}
        )
        assert resp.status == 400
    finally:
        await client.close()


async def test_report_404_when_nothing_matches(tmp_path):
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/v1/report", params={"device_id": "nonexistent"})
        assert resp.status == 404
    finally:
        await client.close()


async def test_report_rejects_bad_format(tmp_path):
    client = await _client(tmp_path)
    try:
        resp = await client.get("/api/v1/report", params={"format": "xml"})
        assert resp.status == 400
    finally:
        await client.close()


async def test_report_sliding_scale_requires_profile(tmp_path):
    sliding_cfg = replace(DEFAULT_REPORT_CONFIG, include_sliding_scale=True)
    client = await _client(tmp_path, report_config=sliding_cfg)
    try:
        resp = await client.get("/api/v1/report", params={"device_id": "Trividia-BLU-11111111"})
        assert resp.status == 400
        data = await resp.json()
        assert "include_sliding_scale" in data["error"]
    finally:
        await client.close()


async def test_report_sliding_scale_with_profile_succeeds(tmp_path):
    sliding_cfg = replace(DEFAULT_REPORT_CONFIG, include_sliding_scale=True)
    client = await _client(tmp_path, report_config=sliding_cfg)
    try:
        resp = await client.get(
            "/api/v1/report", params={"profile": "Alice", "format": "csv"}
        )
        assert resp.status == 200
        body = (await resp.read()).decode()
        assert "Dose (units)" in body
    finally:
        await client.close()
