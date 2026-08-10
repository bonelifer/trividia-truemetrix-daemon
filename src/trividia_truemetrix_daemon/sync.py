"""Poll for docked TRUE METRIX meters and sync their readings to storage.

Unlike the BLE daemons in this family (always-on, scan-for-advertisement),
a TRUE METRIX meter is a fingerstick device you dock occasionally over USB
HID. There's no "connect and stream" here: each poll tick checks which
matching HID devices are currently present (trividia_truemetrix_hid.discover)
and syncs any that weren't already synced during this continuous dock --
see PollLoop's docstring for the presence bookkeeping.
"""

from __future__ import annotations

import asyncio
import datetime
import logging

from trividia_truemetrix_hid import Reading, TrueMetrixClient, discover
from trividia_truemetrix_hid.client import TrueMetrixError

from . import onboarding
from .assignments import AssignmentStore
from .config import ApiConfig, DEFAULT_MQTT_CONFIG, MqttConfig, OnboardingConfig, ProfilesConfig
from .mqtt import publish_reading
from .storage import ReadingStore

_LOGGER = logging.getLogger(__name__)


def _sync_device_blocking(path: bytes, store: ReadingStore) -> tuple[str, str, list[Reading]]:
    """Blocking HID I/O: connect, download, store. Run via asyncio.to_thread.

    Returns (device_id, model, newly-inserted readings) -- the readings
    are what get published to MQTT, if enabled.
    """
    with TrueMetrixClient(path=path) as client:
        info = client.get_device_info()
        readings = client.get_readings()

        synced_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        new_readings = []
        for reading in readings:
            row_id = store.record(
                device_id=info.device_id,
                model=info.model,
                device_time=reading.device_time.isoformat(),
                value_mg_dl=reading.value_mg_dl,
                out_of_range=reading.out_of_range,
                is_control_solution=reading.is_control_solution,
                raw=reading.raw,
                synced_at=synced_at,
            )
            if row_id is not None:
                new_readings.append(reading)

        return info.device_id, info.model, new_readings


async def sync_device(
    path: bytes,
    store: ReadingStore,
    onboarding_config: OnboardingConfig,
    profiles_config: ProfilesConfig,
    assignments: AssignmentStore,
    api_config: ApiConfig,
    mqtt_client=None,
    mqtt_config: MqttConfig = DEFAULT_MQTT_CONFIG,
) -> None:
    """Sync one docked meter, publish new readings to MQTT, and run the onboarding check."""
    try:
        device_id, model, new_readings = await asyncio.to_thread(
            _sync_device_blocking, path, store
        )
    except TrueMetrixError as exc:
        _LOGGER.warning("Sync failed for HID path %r: %s", path, exc)
        return

    _LOGGER.info("Synced %s (%s): %d new reading(s)", device_id, model, len(new_readings))

    if mqtt_client is not None:
        for reading in new_readings:
            await publish_reading(mqtt_client, mqtt_config, device_id, model, reading)

    await onboarding.check_device(
        device_id, model, onboarding_config, profiles_config, assignments, api_config
    )


class PollLoop:
    """Polls for connected TRUE METRIX meters and syncs newly-docked ones.

    A HID path is synced once per continuous dock: after a successful (or
    failed) sync attempt, the path is remembered until it disappears from
    ``discover()`` -- i.e. the meter is undocked -- so it isn't re-synced on
    every poll tick while it stays plugged in. Re-docking the same meter
    later triggers a fresh sync, which is safe (and cheap) even if it has no
    new readings, since ReadingStore.record() dedupes.
    """

    def __init__(
        self,
        store: ReadingStore,
        onboarding_config: OnboardingConfig,
        profiles_config: ProfilesConfig,
        assignments: AssignmentStore,
        api_config: ApiConfig,
        poll_interval_seconds: float,
        mqtt_client=None,
        mqtt_config: MqttConfig = DEFAULT_MQTT_CONFIG,
    ) -> None:
        self._store = store
        self._onboarding_config = onboarding_config
        self._profiles_config = profiles_config
        self._assignments = assignments
        self._api_config = api_config
        self._poll_interval_seconds = poll_interval_seconds
        self._mqtt_client = mqtt_client
        self._mqtt_config = mqtt_config
        self._seen_paths: set[bytes] = set()

    async def _tick(self) -> None:
        present = {entry["path"] for entry in discover()}

        for path in present - self._seen_paths:
            self._seen_paths.add(path)
            await sync_device(
                path,
                self._store,
                self._onboarding_config,
                self._profiles_config,
                self._assignments,
                self._api_config,
                self._mqtt_client,
                self._mqtt_config,
            )

        self._seen_paths &= present

    async def run(self, stop_event: asyncio.Event) -> None:
        """Poll until stop_event is set."""
        while not stop_event.is_set():
            await self._tick()
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=self._poll_interval_seconds
                )
            except asyncio.TimeoutError:
                pass

    async def run_once(self, timeout_seconds: float) -> bool:
        """Poll until at least one meter syncs, or timeout_seconds elapses.

        Returns True if a sync happened. For --once, run right before/while
        docking a meter, instead of running the daemon continuously.
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout_seconds
        while loop.time() < deadline:
            before = set(self._seen_paths)
            await self._tick()
            if self._seen_paths - before:
                return True
            await asyncio.sleep(min(1.0, self._poll_interval_seconds))
        return False
