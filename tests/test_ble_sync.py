from __future__ import annotations

import datetime
from types import SimpleNamespace

from trividia_truemetrix_ble import DeviceInfo, Reading

from trividia_truemetrix_daemon import ble_sync
from trividia_truemetrix_daemon.assignments import AssignmentStore
from trividia_truemetrix_daemon.config import (
    DEFAULT_API_CONFIG,
    DEFAULT_BLE_CONFIG,
    DEFAULT_ONBOARDING_CONFIG,
    DEFAULT_PROFILES_CONFIG,
)
from trividia_truemetrix_daemon.storage import ReadingStore


def _reading(sequence_number: int, value_mg_dl: int = 120) -> Reading:
    return Reading(
        sequence_number=sequence_number,
        value_mg_dl=value_mg_dl,
        device_time=datetime.datetime(2026, 1, 1, 12, 0, 0),
        sample_type="Undetermined Plasma",
        sample_location="Finger",
        is_control_solution=False,
        raw=bytes([0x12, sequence_number & 0xFF]),
    )


def _device_info(serial_number: str | None = "12345678") -> DeviceInfo:
    return DeviceInfo(
        manufacturer="Trividia Health",
        model="TRUE METRIX AIR",
        serial_number=serial_number,
        firmware_version="1.0",
        software_version="1.0",
        address="AA:BB:CC:DD:EE:FF",
        name="NiproBGM",
    )


def _make_fake_client_class(info: DeviceInfo, readings: list[Reading]):
    class _FakeClient:
        def __init__(self, address, *, name=None, connect_timeout=15.0, silence_timeout=3.0):
            self.address = address
            self.name = name

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        async def get_device_info(self):
            return info

        async def get_readings(self, *, include_control_solution=False):
            return readings

    return _FakeClient


async def test_sync_ble_device_async_stores_readings_with_serial_based_device_id(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        ble_sync,
        "TrueMetrixBleClient",
        _make_fake_client_class(_device_info("12345678"), [_reading(1), _reading(2)]),
    )
    store = ReadingStore(str(tmp_path / "readings.db"))

    device_id, model, new_readings = await ble_sync._sync_ble_device_async(
        "AA:BB:CC:DD:EE:FF", "NiproBGM", store, silence_timeout_seconds=3.0
    )

    assert device_id == "Trividia-BLE-12345678"
    assert model == "TRUE METRIX AIR"
    assert len(new_readings) == 2


async def test_sync_ble_device_async_falls_back_to_address_when_no_serial(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ble_sync,
        "TrueMetrixBleClient",
        _make_fake_client_class(_device_info(None), [_reading(1)]),
    )
    store = ReadingStore(str(tmp_path / "readings.db"))

    device_id, _, _ = await ble_sync._sync_ble_device_async(
        "AA:BB:CC:DD:EE:FF", "NiproBGM", store, silence_timeout_seconds=3.0
    )

    assert device_id == "Trividia-BLE-AABBCCDDEEFF"


async def test_sync_ble_device_async_dedupes_on_resync(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ble_sync,
        "TrueMetrixBleClient",
        _make_fake_client_class(_device_info(), [_reading(1)]),
    )
    store = ReadingStore(str(tmp_path / "readings.db"))

    _, _, first = await ble_sync._sync_ble_device_async(
        "AA:BB:CC:DD:EE:FF", "NiproBGM", store, silence_timeout_seconds=3.0
    )
    _, _, second = await ble_sync._sync_ble_device_async(
        "AA:BB:CC:DD:EE:FF", "NiproBGM", store, silence_timeout_seconds=3.0
    )

    assert len(first) == 1
    assert len(second) == 0  # same reading, already stored


async def test_ble_scan_loop_syncs_new_device_once_then_skips_while_present(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ble_sync,
        "TrueMetrixBleClient",
        _make_fake_client_class(_device_info(), [_reading(1)]),
    )

    device = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name="NiproBGM")
    call_count = 0

    async def fake_discover(timeout):
        return [device]

    monkeypatch.setattr(ble_sync, "discover", fake_discover)

    sync_calls = []
    original_sync = ble_sync.sync_ble_device

    async def counting_sync(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        sync_calls.append(args[0])
        await original_sync(*args, **kwargs)

    monkeypatch.setattr(ble_sync, "sync_ble_device", counting_sync)

    store = ReadingStore(str(tmp_path / "readings.db"))
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    loop = ble_sync.BleScanLoop(
        store,
        DEFAULT_ONBOARDING_CONFIG,
        DEFAULT_PROFILES_CONFIG,
        assignments,
        DEFAULT_API_CONFIG,
        DEFAULT_BLE_CONFIG,
    )

    await loop._tick()
    await loop._tick()

    assert call_count == 1
    assert sync_calls == ["AA:BB:CC:DD:EE:FF"]


async def test_ble_scan_loop_resyncs_after_device_disappears_and_returns(monkeypatch, tmp_path):
    monkeypatch.setattr(
        ble_sync,
        "TrueMetrixBleClient",
        _make_fake_client_class(_device_info(), [_reading(1)]),
    )

    device = SimpleNamespace(address="AA:BB:CC:DD:EE:FF", name="NiproBGM")
    present = [True]

    async def fake_discover(timeout):
        return [device] if present[0] else []

    monkeypatch.setattr(ble_sync, "discover", fake_discover)

    store = ReadingStore(str(tmp_path / "readings.db"))
    assignments = AssignmentStore(str(tmp_path / "assignments.json"))
    loop = ble_sync.BleScanLoop(
        store,
        DEFAULT_ONBOARDING_CONFIG,
        DEFAULT_PROFILES_CONFIG,
        assignments,
        DEFAULT_API_CONFIG,
        DEFAULT_BLE_CONFIG,
    )

    await loop._tick()
    assert loop._seen_addresses == {"AA:BB:CC:DD:EE:FF"}

    present[0] = False
    await loop._tick()
    assert loop._seen_addresses == set()

    present[0] = True
    await loop._tick()
    assert loop._seen_addresses == {"AA:BB:CC:DD:EE:FF"}
