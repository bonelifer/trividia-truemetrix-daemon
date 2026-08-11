"""Scan for advertising TRUE METRIX AIR meters and sync their readings to storage.

Runs alongside sync.py's USB HID PollLoop, not instead of it -- a meter
can be synced over either transport, and both write to the same
ReadingStore. Requires the ``ble`` extra (``pip install
trividia-truemetrix-daemon[ble]``); this module is only imported by
cli.py when ``[ble] enabled = yes``, so a HID-only install never needs
trividia_truemetrix_ble installed at all.

The meter behaves the same way over BLE as it does docked over USB HID:
each connection gets its *entire* on-device history, not just readings
added since the last sync -- see trividia_truemetrix_ble's protocol
notes. So, same as sync.py, this treats "sync once per continuous
presence" as the right unit of work: an address is remembered once
synced and not re-synced on every scan while the meter stays nearby,
matching PollLoop's HID-path bookkeeping.

The one real difference from HID: trividia_truemetrix_hid.discover() is
a synchronous, near-instant enumerate, cheap enough to call on every poll
tick. trividia_truemetrix_ble.discover() is a real async BLE scan that
takes scan_timeout_seconds of wall-clock time, so BleScanLoop polls on
its own, typically longer, poll_interval_seconds rather than sharing
PollLoop's -- see BleConfig.
"""

from __future__ import annotations

import asyncio
import datetime
import logging

from trividia_truemetrix_ble import Reading, TrueMetrixBleClient, discover
from trividia_truemetrix_ble import TrueMetrixError

from . import onboarding
from .assignments import AssignmentStore
from .config import (
    ApiConfig,
    BleConfig,
    DEFAULT_MQTT_CONFIG,
    MqttConfig,
    OnboardingConfig,
    ProfilesConfig,
)
from .mqtt import publish_reading
from .storage import ReadingStore

_LOGGER = logging.getLogger(__name__)


async def _sync_ble_device_async(
    address: str,
    name: str | None,
    store: ReadingStore,
    silence_timeout_seconds: float,
) -> tuple[str, str, list[Reading]]:
    """Async BLE I/O: connect, download, store.

    Returns (device_id, model, newly-inserted readings) -- same shape as
    sync._sync_device_blocking's return value, so sync_ble_device can
    follow the same MQTT-publish/onboarding-check pattern as HID's
    sync_device. Unlike HID, this runs directly on the event loop: BLE is
    already async, so there's no asyncio.to_thread wrapper needed here.

    device_id is synthesized as "Trividia-BLE-<serial>" when the meter's
    Device Information Service exposes a serial number, falling back to
    the BLE address itself (colons stripped) when it doesn't -- unlike
    HID's fixed "Trividia-<model_code>-<serial>" shape, BLE's DeviceInfo
    has no equivalent short model code to build from.
    """
    async with TrueMetrixBleClient(
        address, name=name, silence_timeout=silence_timeout_seconds
    ) as client:
        info = await client.get_device_info()
        readings = await client.get_readings()

        device_id = (
            f"Trividia-BLE-{info.serial_number}"
            if info.serial_number
            else f"Trividia-BLE-{address.replace(':', '')}"
        )
        model = info.model or "TRUE METRIX AIR"

        synced_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        new_readings = []
        for reading in readings:
            row_id = store.record(
                device_id=device_id,
                model=model,
                device_time=reading.device_time.isoformat(),
                value_mg_dl=reading.value_mg_dl,
                out_of_range=None,
                is_control_solution=reading.is_control_solution,
                raw=reading.raw.hex(),
                synced_at=synced_at,
            )
            if row_id is not None:
                new_readings.append(reading)

        return device_id, model, new_readings


async def sync_ble_device(
    address: str,
    name: str | None,
    store: ReadingStore,
    onboarding_config: OnboardingConfig,
    profiles_config: ProfilesConfig,
    assignments: AssignmentStore,
    api_config: ApiConfig,
    silence_timeout_seconds: float,
    mqtt_client=None,
    mqtt_config: MqttConfig = DEFAULT_MQTT_CONFIG,
) -> None:
    """Sync one BLE-discovered meter, publish new readings to MQTT, and run the onboarding check.

    Catches more than just TrueMetrixError (this package's own protocol
    errors): a BLE connection is meaningfully flakier than a docked USB
    device (out of range between discovery and connect, timeout, GATT
    errors from bleak/dbus-fast), and none of that is TrueMetrixError.
    Same reasoning as BleScanLoop._tick's scan-failure catch -- an
    unhandled exception here would propagate up through asyncio.gather
    and take down the sibling HID loop too (see cli.run_daemon).
    """
    try:
        device_id, model, new_readings = await _sync_ble_device_async(
            address, name, store, silence_timeout_seconds
        )
    except TrueMetrixError as exc:
        _LOGGER.warning("BLE sync failed for %s: %s", address, exc)
        return
    except Exception as exc:  # noqa: BLE001 - see docstring
        _LOGGER.warning("BLE sync failed for %s: %s", address, exc)
        return

    _LOGGER.info(
        "Synced %s (%s) over BLE: %d new reading(s)", device_id, model, len(new_readings)
    )

    if mqtt_client is not None:
        for reading in new_readings:
            await publish_reading(mqtt_client, mqtt_config, device_id, model, reading)

    await onboarding.check_device(
        device_id, model, onboarding_config, profiles_config, assignments, api_config
    )


class BleScanLoop:
    """Periodically scans for advertising TRUE METRIX AIR meters and syncs newly-seen ones.

    Same "sync once per continuous presence" bookkeeping as sync.PollLoop,
    keyed by BLE address instead of a HID path: once synced, an address
    is remembered until a scan no longer finds it advertising, so a meter
    sitting nearby doesn't get re-synced on every scan tick. It
    reappearing later (e.g. after a new reading) triggers a fresh sync,
    safe even with no new readings, since ReadingStore.record() dedupes.
    """

    def __init__(
        self,
        store: ReadingStore,
        onboarding_config: OnboardingConfig,
        profiles_config: ProfilesConfig,
        assignments: AssignmentStore,
        api_config: ApiConfig,
        ble_config: BleConfig,
        mqtt_client=None,
        mqtt_config: MqttConfig = DEFAULT_MQTT_CONFIG,
    ) -> None:
        self._store = store
        self._onboarding_config = onboarding_config
        self._profiles_config = profiles_config
        self._assignments = assignments
        self._api_config = api_config
        self._ble_config = ble_config
        self._mqtt_client = mqtt_client
        self._mqtt_config = mqtt_config
        self._seen_addresses: set[str] = set()

    async def _tick(self) -> None:
        try:
            devices = await discover(timeout=self._ble_config.scan_timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - any BLE adapter/D-Bus failure
            # Unlike sync_ble_device's TrueMetrixError catch (a known,
            # specific failure mode), a scan itself can fail in ways that
            # vary by platform/backend (missing D-Bus, adapter powered
            # off, permission denied) -- none of bleak's own. This must
            # not propagate: an asyncio.gather sibling failure here would
            # cancel the working HID loop too (see cli.run_daemon), so a
            # bad scan is treated as "found nothing this tick" instead.
            _LOGGER.warning("BLE scan failed: %s", exc)
            return

        present = {device.address for device in devices}

        for device in devices:
            if device.address in self._seen_addresses:
                continue
            self._seen_addresses.add(device.address)
            await sync_ble_device(
                device.address,
                device.name,
                self._store,
                self._onboarding_config,
                self._profiles_config,
                self._assignments,
                self._api_config,
                self._ble_config.silence_timeout_seconds,
                self._mqtt_client,
                self._mqtt_config,
            )

        self._seen_addresses &= present

    async def run(self, stop_event: asyncio.Event) -> None:
        """Scan until stop_event is set."""
        while not stop_event.is_set():
            await self._tick()
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=self._ble_config.poll_interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def run_once(self, timeout_seconds: float) -> bool:
        """Scan until at least one meter syncs, or timeout_seconds elapses.

        Returns True if a sync happened. For --once, run right before/while
        the meter is nearby and advertising, instead of running the daemon
        continuously.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            before = set(self._seen_addresses)
            await self._tick()
            if self._seen_addresses - before:
                return True
            await asyncio.sleep(min(1.0, self._ble_config.poll_interval_seconds))
        return False
