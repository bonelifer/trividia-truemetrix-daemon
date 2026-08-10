# Project notes for trividia-truemetrix-daemon

## Related repos to watch

- **trividia-truemetrix-hid** --
  https://github.com/bonelifer/trividia-truemetrix-hid -- this daemon's
  own USB HID protocol library, pulled as a `git+https` dependency in
  `pyproject.toml` (not a versioned PyPI release). A fix or feature added
  there doesn't reach this daemon automatically: it needs
  `pip install --upgrade` (or a fresh `docker build`, which always
  re-clones at build time) to pick it up. See that repo's own `CLAUDE.md`
  for the upstream Tidepool source *it* tracks -- a protocol fix there
  flows through this one too, eventually.

- **etekcity-scale-daemon** -- local checkout at `../etekcity-scale-daemon`,
  https://github.com/bonelifer/etekcity-scale-daemon -- the architecture
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

Sync/report/alerting/MQTT/API logic is unit-tested (116 tests as of this
writing), and the Docker image is CI-verified end to end (real `docker
build` + `docker run`, not just "`pip install .` succeeds" -- see
`.github/workflows/ci.yml`). The one thing none of that touches: the
actual USB HID hardware path -- docking a real TRUE METRIX AIR and
syncing readings through this daemon -- which no CI runner can exercise
and hasn't been tested yet.
