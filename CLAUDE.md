# Project notes for trividia-truemetrix-daemon

## Related repos to watch

- **trividia-truemetrix-hid** --
  https://github.com/home-health-hub/trividia-truemetrix-hid -- this daemon's
  own USB HID protocol library, pulled as a `git+https` dependency in
  `pyproject.toml` (not a versioned PyPI release). A fix or feature added
  there doesn't reach this daemon automatically: it needs
  `pip install --upgrade` (or a fresh `docker build`, which always
  re-clones at build time) to pick it up. See that repo's own `CLAUDE.md`
  for the upstream Tidepool source *it* tracks -- a protocol fix there
  flows through this one too, eventually.

- **trividia-truemetrix-ble** --
  https://github.com/home-health-hub/trividia-truemetrix-ble -- this daemon's
  optional direct-BLE protocol library for TRUE METRIX AIR, pulled as a
  `git+https` dependency behind the `ble` extra (`pip install
  trividia-truemetrix-daemon[ble]`), not installed by default. Same
  upgrade caveat as trividia-truemetrix-hid above: a fix there needs
  `pip install --upgrade "trividia-truemetrix-daemon[ble]"` to reach this
  daemon. Wired up in `ble_sync.py` (`BleScanLoop`, mirroring
  `sync.py`'s `PollLoop` shape but polling on its own interval, since a
  BLE scan takes real wall-clock time unlike HID's near-instant
  enumerate) and `cli.py`'s `run_daemon` (runs alongside the HID
  `PollLoop` via `asyncio.gather` when `[ble] enabled = yes`). Two
  integration points worth knowing about if touching this code: (1)
  `mqtt.publish_reading` uses `getattr(reading, "out_of_range", None)`
  rather than a direct attribute access, since a
  `trividia_truemetrix_ble.Reading` has no `out_of_range` field (the
  standard Bluetooth Glucose Profile doesn't expose the meter's own
  HI/LO clamping the way the HID protocol does) -- storage.py's schema
  already had this column nullable, so no migration was needed; (2)
  `BleScanLoop._tick` and `sync_ble_device` both catch broad `Exception`,
  not just `TrueMetrixError` -- a real BLE scan/connection failure (no
  D-Bus, adapter off, permission denied, device moved out of range) can
  raise all sorts of exceptions from bleak/dbus-fast that aren't this
  package's own error type, and an unhandled one here would propagate
  through `asyncio.gather` and take down the sibling HID loop too, not
  just the BLE path. Confirmed this concretely, not just reasoned about
  it: an early version crashed exactly this way in a container with no
  D-Bus socket at all, before both catches were broadened.

- **etekcity-scale-daemon** -- local checkout at `../etekcity-scale-daemon`,
  https://github.com/home-health-hub/etekcity-scale-daemon -- the architecture
  template this daemon's conventions were deliberately mirrored from
  (config/storage/alerting/MQTT/pruning/Docker/CI patterns, notification
  throttling shapes, etc.). Not a code dependency, just a design
  reference: if that project adopts a new pattern worth borrowing, or
  fixes a bug in a pattern this daemon copied verbatim, it's worth
  checking.

- **Tidepool's uploader repo** -- https://github.com/tidepool-org/uploader
  -- the true origin of the protocol this whole stack is built on. This
  daemon doesn't depend on it directly (trividia-truemetrix-hid does, and
  tracks it in detail in its own `CLAUDE.md` -- exact file, last-known
  commit, etc.), but it's worth knowing it's there at the root: if
  Trividia/Tidepool ever add real ketone support, fix a device-time bug,
  or add a new TRUE METRIX variant, it surfaces here two hops later, via
  a `trividia-truemetrix-hid` update.

## Verification status

Sync/report/alerting/MQTT/API/BLE logic is unit-tested (129 tests as of
this writing, `.[dev,ble]` installed in CI so the BLE path is actually
covered, not skipped), and the Docker image is CI-verified end to end
(real `docker build` + `docker run`, not just "`pip install .`
succeeds" -- see `.github/workflows/ci.yml`). What none of that touches:
real hardware, over either transport. Docking a real TRUE METRIX AIR and
syncing over USB HID, and connecting to one over BLE and syncing through
`BleScanLoop`, are both unverified -- no CI runner can exercise either.
The BLE *protocol* itself (byte format, GATT behavior) has real-hardware
verification already, done in `trividia-truemetrix-ble`'s own repo, not
this daemon's -- this daemon's BLE *wiring* on top of that (device_id
synthesis, storage, MQTT, onboarding) is what's still unconfirmed
end-to-end.

Docker is USB HID only -- the `ble` extra isn't in the default image, and
reaching a host's Bluetooth adapter from inside a container needs its
own passthrough setup this project doesn't provide yet. See the
README's Docker section.
