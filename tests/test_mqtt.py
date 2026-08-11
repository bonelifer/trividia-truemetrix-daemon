from __future__ import annotations

import dataclasses
import datetime
import json
from types import SimpleNamespace

from trividia_truemetrix_daemon.config import DEFAULT_MQTT_CONFIG
from trividia_truemetrix_daemon.mqtt import publish_reading


class _FakeClient:
    def __init__(self):
        self.published: list[tuple[str, str, int, bool]] = []

    async def publish(self, topic, payload, qos, retain):
        self.published.append((topic, payload, qos, retain))


async def test_publish_reading_includes_out_of_range_for_hid_readings():
    client = _FakeClient()
    reading = SimpleNamespace(
        device_time=datetime.datetime(2026, 1, 1, 12, 0, 0),
        value_mg_dl=120,
        out_of_range=None,
    )

    await publish_reading(
        client, DEFAULT_MQTT_CONFIG, "Trividia-BLU-123", "TRUE METRIX AIR", reading
    )

    assert len(client.published) == 1
    topic, payload, _, _ = client.published[0]
    assert topic == f"{DEFAULT_MQTT_CONFIG.topic_prefix}/Trividia-BLU-123/state"
    body = json.loads(payload)
    assert body["out_of_range"] is None
    assert body["value_mg_dl"] == 120


async def test_publish_reading_defaults_out_of_range_to_none_for_ble_readings():
    # A trividia_truemetrix_ble.Reading has no out_of_range attribute at
    # all -- this must not raise AttributeError, unlike a direct
    # reading.out_of_range access would.
    client = _FakeClient()
    reading = SimpleNamespace(
        device_time=datetime.datetime(2026, 1, 1, 12, 0, 0),
        value_mg_dl=120,
    )

    await publish_reading(
        client, DEFAULT_MQTT_CONFIG, "Trividia-BLE-abc123", "TRUE METRIX AIR", reading
    )

    body = json.loads(client.published[0][1])
    assert body["out_of_range"] is None


async def test_publish_reading_uses_configured_qos_and_retain():
    client = _FakeClient()
    config = dataclasses.replace(DEFAULT_MQTT_CONFIG, qos=1, retain=False)
    reading = SimpleNamespace(
        device_time=datetime.datetime(2026, 1, 1, 12, 0, 0), value_mg_dl=100, out_of_range="high"
    )

    await publish_reading(client, config, "dev-1", "TRUE METRIX AIR", reading)

    _, _, qos, retain = client.published[0]
    assert qos == 1
    assert retain is False
