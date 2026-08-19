# trividia-truemetrix-daemon

![trividia-truemetrix-daemon: glucose readings over Bluetooth or USB to a local home server and database](docs/images/trividia-truemetrix-daemon-banner.png)

A standalone Linux daemon that syncs a Trividia Health TRUE METRIX blood
glucose meter's stored readings to a local SQLite database over USB HID or,
optionally, Bluetooth LE. No cloud account, no companion app, no Tidepool
account required.

It's a thin wrapper around
[`trividia-truemetrix-hid`](https://github.com/home-health-hub/trividia-truemetrix-hid)
(the primary, always-available USB HID transport) and, when enabled,
[`trividia-truemetrix-ble`](https://github.com/home-health-hub/trividia-truemetrix-ble)
(direct BLE for TRUE METRIX AIR, no docking station needed), packaged to
run unattended as a `systemd` service.

**Disclaimer: This is an unofficial, community-developed project. It is not
affiliated with, officially maintained by, or in any way officially
connected with Trividia Health or Tidepool. This is a personal-use tool for
reading data from your own meter(s), not a medical product. TRUE METRIX is
a fingerstick meter, not a continuous glucose monitor -- this daemon only
learns about a reading once the meter is physically docked and synced,
never in real time. Don't rely on it for real-time hypo/hyperglycemia
alerting; read your meter's own display for that. The optional sliding-scale
dosing display (see [Reports](#reports)) only looks up a table you
configure yourself -- it must come from the person's own doctor, and this
tool does not generate, validate, or recommend doses.**

## Supported meters

Over USB HID: whatever
[`trividia-truemetrix-hid`](https://github.com/home-health-hub/trividia-truemetrix-hid)
supports -- TRUE METRIX, TRUE METRIX GO, and TRUE METRIX AIR. Only TRUE
METRIX AIR has real-hardware testing at time of writing -- see that
library's README for the current verification status.

Over Bluetooth LE (optional, see [below](#bluetooth-le-optional)): TRUE
METRIX AIR only, via
[`trividia-truemetrix-ble`](https://github.com/home-health-hub/trividia-truemetrix-ble).

## Features

- Polls for a docked meter over USB HID and syncs its stored readings to a
  local SQLite database, deduplicated so re-docking the same meter is
  always safe (the meter returns its *entire* history every sync, not just
  new readings -- see [Database schema](#database-schema)).
- Optional direct-BLE sync for TRUE METRIX AIR, running alongside the USB
  HID poll loop -- no docking station needed. Same dedupe guarantee, same
  database. See [Bluetooth LE](#bluetooth-le-optional).
- **Multi-meter, per-person attribution.** Unlike a shared BLE scale, each
  meter has its own serial number, so `[profile.<name>]` sections bind a
  person to their own meter(s) by `device_id` -- no runtime "who was this?"
  tagging needed once configured.
- **New-device onboarding.** The first time an unrecognized meter syncs, an
  interactive assignment prompt (ntfy with one action button per profile,
  or a local `dunstify` prompt) and an unconditional admin notification
  (via [Apprise](https://github.com/caronc/apprise)) both fire once, then
  go quiet for that device -- see [Onboarding](#onboarding-new-devices).
- `trividia-truemetrix-find-unassigned` lists any device_id with stored
  readings but no profile, as a durable fallback for anything the live
  notifications missed.
- Runs as a `systemd` service with automatic restart on failure.
- `trividia-truemetrix-report` generates a PDF or CSV table (or line chart)
  of readings -- see [Reports](#reports).
- `trividia-truemetrix-alert-check` notifies via
  [Apprise](https://github.com/caronc/apprise) on high/low glucose
  thresholds or a meter going stale -- see [Alerting](#alerting).
- A local, read-only HTTP API (`/api/v1/latest`, on-demand `/api/v1/report`) for fetching
  data without shelling in -- see [HTTP API](#http-api).
- Optional MQTT publishing of each newly-synced reading, as JSON -- see
  [MQTT](#mqtt).
- `trividia-truemetrix-prune` manually deletes readings older than a given
  number of days -- see [Pruning old data](#pruning-old-data).
- A Docker image, CI-built and published to GHCR -- see [Docker](#docker).

## Requirements

Requires Python 3.11+, and everything
[`trividia-truemetrix-hid`](https://github.com/home-health-hub/trividia-truemetrix-hid#requirements)
does: the `hidapi` system library, and (on Linux) a udev rule for non-root
USB HID access. The daemon runs as its own system user in the `plugdev`
group, so use `GROUP="plugdev"` rather than the library README's
simpler `MODE="0666"` example:

```
# /etc/udev/rules.d/99-truemetrix.rules
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="1f41", GROUP="plugdev", MODE="0660"
```

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

USB HID is the only hard requirement -- Bluetooth LE support is optional
and off by default; see [Bluetooth LE](#bluetooth-le-optional) for its
own separate requirements.

## Installation

```bash
git clone https://github.com/home-health-hub/trividia-truemetrix-daemon.git
cd trividia-truemetrix-daemon
sudo ./install.sh
```

This creates a venv at `/opt/trividia-truemetrix-daemon`, installs the
package (pulling in `trividia-truemetrix-hid` from GitHub), seeds
`/etc/trividia-truemetrix-daemon/config.ini` (if it doesn't already exist),
creates a `trividia-truemetrix-daemon` system user in the `plugdev` group,
and installs and enables the systemd service. It also installs (but does
not enable) the device-assignment API unit. Safe to re-run. Edit the config
and `sudo systemctl restart trividia-truemetrix-daemon` afterward.

### Config file

Copy the example config and edit it:

```bash
sudo mkdir -p /etc/trividia-truemetrix-daemon
sudo cp config/trividia-truemetrix-daemon.ini.example /etc/trividia-truemetrix-daemon/config.ini
sudo "$EDITOR" /etc/trividia-truemetrix-daemon/config.ini
```


| Section | Key | Description |
|---|---|---|
| `daemon` | `log_level` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `daemon` | `poll_interval_seconds` | How often to check for a newly-docked meter. Default `5`. |
| `storage` | `db_path` | Path to the SQLite database file. |
| `profile.<name>` | `device_ids` | Comma-separated `device_id`s (e.g. `Trividia-BLU-12345678`) this person's meter(s) report as. Required; each device_id may belong to only one profile -- a config with two profiles claiming the same one fails to load. |
| `profile.<name>` | `name` | Optional full display name (e.g. `Alice Smith`). Defaults to the section id (`<name>` in `[profile.<name>]`) if left blank. The section id itself -- not this field -- is what's used for matching, action-button labels, and `?profile=`. |
| `profile.<name>` | `email` | Optional, unused for now (no report header uses it yet). |
| `profile.<name>` | `notes` | Optional free-text note (e.g. diagnosis, insulin type). Shown in the PDF report header (after Meter) whenever `--profile`/`?profile=` selects this profile; not shown in CSV. |
| `profile.<name>` | `sliding_scale` | Optional dosing table, one band per line: `low:high:dose[:label]` (`low`/`high` blank = unbounded on that side). See [Reports](#reports) -- **this must come from the person's own doctor; this tool never invents, validates, or adjusts these numbers.** Overlapping bands are rejected at load (an ambiguous table is a safety issue). |
| `profile.<name>` | `high_threshold_mg_dl` / `low_threshold_mg_dl` | Optional, overrides `[alerting]`'s global thresholds for this profile's meter(s). Blank = use the global default. See [Alerting](#alerting). |
| `profile.<name>` | `tir_low_mg_dl` / `tir_high_mg_dl` | Optional, overrides `[report]`'s global Time in Range target band for this profile's meter(s). Blank = use the global default (70-180 mg/dL). See [Reports](#reports). |
| `ble` | `enabled` | Run the BLE scan loop alongside USB HID: `yes` or `no`. Defaults to `no`. Requires the `ble` extra -- see [Bluetooth LE](#bluetooth-le-optional). |
| `ble` | `poll_interval_seconds` | Seconds between BLE scan attempts. Defaults to `30` -- separate from `daemon.poll_interval_seconds` since a BLE scan takes real time, unlike HID's near-instant check. |
| `ble` | `scan_timeout_seconds` | Seconds each individual scan attempt lasts. Defaults to `5`. |
| `ble` | `silence_timeout_seconds` | Seconds of no new notification before concluding the meter finished streaming its history. Defaults to `3`. See `trividia-truemetrix-ble`'s README for why this is a heuristic, not a hard signal. |
| `onboarding` | `enabled` | Fire the new-device onboarding notification: `yes` or `no`. Defaults to `no`. |
| `onboarding` | `ntfy_url` / `ntfy_token` | ntfy topic for the interactive assignment prompt. Requires `[api] enabled = yes`. |
| `onboarding` | `api_base_url` | Where the API is reachable, for ntfy's action buttons to call back into. Only used when `[api]` is enabled. |
| `onboarding` | `dunstify_timeout_seconds` | Seconds to wait for a dunstify response. Only used when `[api]` is disabled. |
| `onboarding` | `admin_apprise_urls` | Comma-separated Apprise URLs for the unconditional admin heads-up (include a `mailto://` URL for email). |
| `onboarding` | `state_path` | Where "already prompted" state is tracked (once per device_id, ever). |
| `onboarding` | `assignments_path` | Where dynamic (button-tap) device_id -> profile assignments are stored, separate from the static config. |
| `api` | `enabled` | Run the local HTTP API: `yes` or `no`. See [HTTP API](#http-api); also needed for the ntfy assignment callback. |
| `api` | `host` / `port` / `token` | Bind address/port, and an optional bearer token required on every endpoint except `/api/v1/health` and `/api/v1/capabilities`. |
| `report` | `unit` | `mg_dl` or `mmol_l`. Defaults to `mg_dl` (what the meter itself always reports). |
| `report` | `date_format` | `us` (MM/DD/YYYY, 12-hour) or `world` (DD/MM/YYYY, 24-hour). |
| `report` | `layout` | `full` (one row per reading), `simple` (date/glucose only, side-by-side columns), or `chart` (a line chart of glucose over time). PDF only. |
| `report` | `page_size` | `letter` or `a4`. PDF only. |
| `report` | `include_device_id` / `include_model` | Show these columns: `yes` or `no`. **CSV only** -- PDF always shows meter identity once in the header instead (a `Meter: <device_id> (<model>)` line), not as per-row columns, to keep the table narrow enough to fit the page. |
| `report` | `include_profile` | Show the Profile column in the `full` layout: `yes` or `no`. Needs `--config` (profile membership isn't in the database). Ignored in PDF whenever the owner is already named elsewhere -- `--profile`'s report (named in the Patient header) or a `--multi-meter` section (named in its heading); only applies to a PDF spanning more than one owner with neither, or to CSV always. |
| `report` | `include_summary` | Print a min/max/average/high-count/low-count summary line below the title: `yes` or `no`. PDF only. |
| `report` | `include_sliding_scale` | Show Dose/Note columns, looked up per reading from a profile's `sliding_scale`: `yes` or `no`. Requires `--profile` (or resolves per section with `--multi-meter`) -- see [Reports](#reports) and its disclaimer. |
| `report` | `include_time_in_range` | Show a below/in-range/above pie chart: `yes` or `no`. Works without `--profile` too, using the global target band; a profile's own `tir_low_mg_dl`/`tir_high_mg_dl` overrides it (resolves per section with `--multi-meter`). |
| `report` | `tir_low_mg_dl` / `tir_high_mg_dl` | Global default Time in Range target band, mg/dL. Defaults to `70`/`180` (a common clinical target range) -- unlike `sliding_scale`, this has a sensible default since it's a well-known public-health guideline, not an individualized prescription. |
| `alerting` | `enabled` | Notify via Apprise on threshold/staleness conditions: `yes` or `no`. Defaults to `no`. |
| `alerting` | `apprise_urls` | Comma-separated Apprise service URLs. Required if `enabled = yes`. |
| `alerting` | `high_threshold_mg_dl` / `low_threshold_mg_dl` | Global default thresholds, mg/dL. `0` disables that check. Overridable per profile -- see above. |
| `alerting` | `stale_after_days` | Alert if a meter hasn't produced a reading in over this many days. `0` disables the check. |
| `alerting` | `state_path` | Where per-meter alert state is persisted (throttles repeat alerts). |
| `mqtt` | `enabled` | Publish each newly-synced reading to MQTT as JSON: `yes` or `no`. Defaults to `no`. |
| `mqtt` | `host` | Broker hostname. Required if `enabled = yes`. |
| `mqtt` | `port` | Broker port. Defaults to `1883`. |
| `mqtt` | `username` / `password` | Optional broker credentials. |
| `mqtt` | `use_tls` | Wrap the connection in TLS: `yes` or `no`. Defaults to `no`. |
| `mqtt` | `topic_prefix` | Messages publish to `<topic_prefix>/<device_id>/state`. Defaults to `trividia_truemetrix_daemon`. |
| `mqtt` | `qos` | MQTT QoS level: `0`, `1`, or `2`. Defaults to `0`. |
| `mqtt` | `retain` | Whether the broker retains the last message for new subscribers: `yes` or `no`. Defaults to `yes`. |

### systemd service

```bash
sudo useradd --system --no-create-home --user-group --groups plugdev trividia-truemetrix-daemon
sudo cp systemd/trividia-truemetrix-daemon.service /etc/systemd/system/
sudo ln -sf /opt/trividia-truemetrix-daemon/venv/bin/trividia-truemetrix-daemon /usr/bin/trividia-truemetrix-daemon
sudo ln -sf /opt/trividia-truemetrix-daemon/venv/bin/trividia-truemetrix-api /usr/bin/trividia-truemetrix-api
sudo ln -sf /opt/trividia-truemetrix-daemon/venv/bin/trividia-truemetrix-find-unassigned /usr/bin/trividia-truemetrix-find-unassigned
sudo ln -sf /opt/trividia-truemetrix-daemon/venv/bin/trividia-truemetrix-report /usr/bin/trividia-truemetrix-report
sudo ln -sf /opt/trividia-truemetrix-daemon/venv/bin/trividia-truemetrix-alert-check /usr/bin/trividia-truemetrix-alert-check
sudo ln -sf /opt/trividia-truemetrix-daemon/venv/bin/trividia-truemetrix-prune /usr/bin/trividia-truemetrix-prune
sudo systemctl daemon-reload
sudo systemctl enable --now trividia-truemetrix-daemon
```

Watch it with:

```bash
sudo journalctl -u trividia-truemetrix-daemon -f
```

## Bluetooth LE (optional)

TRUE METRIX AIR can also sync directly over Bluetooth LE, no docking
station needed, running alongside the USB HID poll loop rather than
replacing it -- a meter can be synced over either transport, and both
write to the same database. Off by default.

Requires the `ble` extra:

```bash
/opt/trividia-truemetrix-daemon/venv/bin/pip install "trividia-truemetrix-daemon[ble]"
```

(or `pip install ".[ble]"` from a source checkout). This pulls in
[`trividia-truemetrix-ble`](https://github.com/home-health-hub/trividia-truemetrix-ble),
which depends on [`bleak`](https://pypi.org/project/bleak/) -- on Linux,
that means BlueZ and D-Bus, already present on most desktop/server
installs but not inside the project's default Docker image (see
[Docker](#docker)). Non-root BLE access on Linux typically needs the
running user in the `bluetooth` group, same spirit as HID's `plugdev`
requirement above.

Then turn it on in the config:

```ini
[ble]
enabled = yes
```

`--check-config` catches the common misconfiguration -- `ble.enabled =
yes` without the extra installed -- and fails clearly instead of the
daemon crashing partway through startup. See the config table above for
`poll_interval_seconds`/`scan_timeout_seconds`/`silence_timeout_seconds`.

A BLE-synced meter's `device_id` is `Trividia-BLE-<serial>` (or
`Trividia-BLE-<address>` if the meter doesn't expose a serial number over
BLE) -- a different shape from USB HID's `Trividia-<model_code>-<serial>`,
since BLE's standard Device Information Service has no equivalent short
model code to build from. The same physical meter therefore gets two
different `device_id`s depending on which transport synced it; add both
to a profile's `device_ids` if you use both transports with the same
meter (see the `profile.<name>` rows in the config table above --
"Bob" already shows the multi-`device_id` pattern for a replaced meter,
same idea applies here).

**Not yet verified against real hardware** -- the underlying protocol
itself has been (see `trividia-truemetrix-ble`'s own README for how), but
this daemon's BLE wiring hasn't been exercised against a real meter yet.

## Onboarding new devices

When a meter with a `device_id` not claimed by any `[profile.<name>]`
syncs for the first time:

1. Its readings are still stored (never dropped) under the raw device_id.
2. An interactive assignment prompt fires: ntfy with one action button per
   configured profile (if `[api] enabled = yes`), or a local `dunstify`
   prompt otherwise -- tapping either binds that device_id to the chosen
   profile going forward, written to `onboarding.assignments_path`.
3. **Unconditionally**, alongside step 2, an admin notification fires via
   every URL in `onboarding.admin_apprise_urls` -- so an admin is informed
   even if the interactive prompt is missed or ignored.
4. Both fire once per device_id, ever (tracked in `onboarding.state_path`).
   There's no repeat nagging -- run `trividia-truemetrix-find-unassigned`
   any time to reconcile anything missed by both channels.

Enable it:

```ini
[onboarding]
enabled = yes
admin_apprise_urls = mailto://user:pass@gmail.com

[api]
enabled = yes
```

```bash
sudo cp systemd/trividia-truemetrix-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trividia-truemetrix-api.service
```

Manually assign (or correct) a device without waiting for a notification:

```bash
curl "http://127.0.0.1:8080/api/v1/assign-device?device_id=Trividia-BLU-12345678&profile=Alice"
```

### Finding unassigned devices

```bash
trividia-truemetrix-find-unassigned --config /etc/trividia-truemetrix-daemon/config.ini
```

## Alerting

Also optional and not enabled by default. `trividia-truemetrix-alert-check`
checks every known meter's most recent reading for two kinds of condition
and notifies via [Apprise](https://github.com/caronc/apprise) when
triggered:

- **High/low threshold**: the latest reading is above `high_threshold_mg_dl`
  or below `low_threshold_mg_dl`. These are practical thresholds you set
  for actionable notification -- distinct from the meter's own fixed
  HI (>600 mg/dL)/LO (<20 mg/dL) display flags, which are already
  emergency-level values by the time they're reached.
- **Staleness**: a meter hasn't produced a reading in over `stale_after_days`
  days.

All three are `0`/disabled by default. Set at least one to a positive
value, plus `apprise_urls`, in `[alerting]`:

```ini
[alerting]
enabled = yes
apprise_urls = tgram://bot_token/chat_id, mailto://user:password@gmail.com
high_threshold_mg_dl = 250
low_threshold_mg_dl = 70
stale_after_days = 2
```

A profile's own `high_threshold_mg_dl`/`low_threshold_mg_dl` (see the
config table above) overrides these globals for that profile's meter(s)
specifically -- useful once more than one person's readings share a
database, since target ranges vary by treatment plan.

Run it periodically with the bundled timer:

```bash
sudo cp systemd/trividia-truemetrix-alert-check.service systemd/trividia-truemetrix-alert-check.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trividia-truemetrix-alert-check.timer
```

Defaults to `OnCalendar=hourly`. A high/low alert only fires once per
newly-arrived reading that crosses the threshold, not on every subsequent
check while that same reading stays the latest one. A repeat staleness
alert is throttled to at most once per day while the condition persists.
State is tracked in `alerting.state_path` (default
`/var/lib/trividia-truemetrix-daemon/alert-state.json`); delete it to reset
throttling.

**This checks the local database, not the meter in real time** -- like
everything else in this daemon, an alert only fires once a meter has
actually been docked and synced (see the top-of-README disclaimer). It is
not, and cannot be, real-time hypo/hyperglycemia protection.

## HTTP API

Also optional and not enabled by default. `trividia-truemetrix-api` runs a
small local HTTP server exposing the same data as the other tools. It
reads the SQLite database directly and works whether or not the daemon is
currently running.

```ini
[api]
enabled = yes
host = 127.0.0.1
port = 8080
token =
```

```bash
sudo cp systemd/trividia-truemetrix-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trividia-truemetrix-api.service
```

Endpoints:

| Method & path | Description |
|---|---|
| `GET /api/v1/health` | Unauthenticated liveness check: `{"status": "ok", "version": "..."}`. |
| `GET /api/v1/capabilities` | Unauthenticated description of what this daemon exposes (measurement types, profile model, timestamp fields, MQTT topic pattern) -- see [Capabilities](#capabilities). |
| `GET /api/v1/latest[?device_id=...&profile=...]` | Most recent reading for each meter (or one, if filtered), as JSON, with `profile` resolved the same way the daemon itself resolves it. |
| `GET /api/v1/report[?format=pdf\|csv&period=...&from=...&to=...&device_id=...&profile=...&multi_meter=1]` | Generates a report on demand using the same `[report]` config as `trividia-truemetrix-report`, returned as a file download. `multi_meter=1` is the query-string equivalent of `--multi-meter`; `report.include_sliding_scale = yes` still requires `?profile=` (or resolves per section under `multi_meter=1`), same as the CLI. |
| `GET`/`POST /api/v1/assign-device?device_id=...&profile=...` | Bind a meter to a profile -- see [Onboarding new devices](#onboarding-new-devices). |

```bash
curl http://127.0.0.1:8080/api/v1/latest
curl -o report.pdf "http://127.0.0.1:8080/api/v1/report?profile=Alice&period=30d"
```

**There's no TLS built in.** `host` defaults to `127.0.0.1` (loopback only)
for a reason: don't bind it to `0.0.0.0` or a LAN-facing interface without
putting a reverse proxy (with TLS and its own auth) in front of it.
Setting `api.token` requires an `Authorization: Bearer <token>` header on
every endpoint except `/api/v1/health` and `/api/v1/capabilities`, which is
worth doing even on loopback if other local users/processes on the same
host shouldn't see glucose data:

```bash
curl -H "Authorization: Bearer <token>" http://127.0.0.1:8080/api/v1/latest
```

### Capabilities

`GET /api/v1/capabilities` describes what this daemon exposes, for a
client (e.g. a Health Hub aggregator) to introspect without hardcoding
daemon-specific assumptions:

```bash
curl http://127.0.0.1:8080/api/v1/capabilities
```

```json
{
  "daemon": "trividia-truemetrix",
  "api_version": "v1",
  "measurement_types": ["glucose"],
  "measurement_modes": ["spot"],
  "profile_model": "assignable",
  "timestamp_fields": {
    "device_time": "The meter's own clock at the time of the reading -- closest equivalent to \"measured at\".",
    "synced_at": "When the daemon ingested the reading -- closest equivalent to \"received at\"."
  },
  "mqtt": {"enabled": false}
}
```

## MQTT

Set `[mqtt] enabled = yes` (plus `host`) to publish each newly-synced
reading to an MQTT broker as JSON, alongside the local SQLite recording:

```ini
[mqtt]
enabled = yes
host = broker.example.com
port = 1883
username = myuser
password = mypassword
use_tls = no
topic_prefix = trividia_truemetrix_daemon
qos = 0
retain = yes
```

Each reading publishes to `<topic_prefix>/<device_id>/state`, e.g.
`trividia_truemetrix_daemon/Trividia-BLU-12345678/state`:

```json
{"device_id": "Trividia-BLU-12345678", "model": "TRUE METRIX AIR", "device_time": "2026-06-15T08:00:00", "value_mg_dl": 95, "out_of_range": null}
```

A broker that's down or unreachable is logged as a warning and otherwise
ignored -- USB HID sync to the local database is the daemon's primary job
and is never blocked by an MQTT outage. Only *newly-inserted* readings are
published (see [Database schema](#database-schema) on why re-syncing a
meter is otherwise a no-op) -- re-docking an already-synced meter doesn't
republish its whole history. Check `--check-config` to confirm the daemon
parsed your `[mqtt]` settings as expected before relying on it.

There's no Home Assistant MQTT discovery support (auto-creating entities):
this publishes raw JSON only. Subscribe and parse it yourself, or wire up
discovery messages separately if you need that.

## Pruning old data

`trividia-truemetrix-prune` deletes readings older than a given number of
days. It's manual only: nothing in the daemon deletes data automatically.
It's a **dry run by default**: it reports how many rows match without
touching anything, until you pass `--yes`.

```bash
# See how many readings older than 365 days would be deleted
trividia-truemetrix-prune --config /etc/trividia-truemetrix-daemon/config.ini --older-than 365

# Actually delete them (also reclaims disk space with VACUUM)
trividia-truemetrix-prune --config /etc/trividia-truemetrix-daemon/config.ini --older-than 365 --yes
```

Add `--device-id Trividia-BLU-12345678` to restrict pruning to one meter.
`--db` works the same as with `trividia-truemetrix-report`, bypassing the
config file. Pruning is keyed on `device_time` (the meter's own clock),
matching what reports and alerting already use -- see [Database
schema](#database-schema).

## Docker

CI actually builds and runs this image (`docker build`, then `--version`
on every console script, `--check-config`, and a real report generation,
all inside the container) on every push, so those specific things are
verified, not just "`pip install .` succeeds." **What CI can't verify is
the USB HID hardware path** -- there's no meter attached to a GitHub
Actions runner, so actually docking a meter and syncing through
`docker-compose`'s device passthrough remains unconfirmed until tested
against real hardware. Treat the container mechanics as working, and the
hardware path as a starting point to debug.

USB HID access from inside a container needs the meter's `/dev/hidrawN`
node, which -- unlike a BLE daemon's one stable D-Bus/adapter path -- is
assigned dynamically per USB enumeration and only exists while the meter
is docked. `docker-compose.yml` uses `privileged: true` plus a full
`/dev:/dev` bind mount as the simplest reliable option for that; it's
broader access than strictly necessary. If your meter's hidraw node is
stable on your system, replace both with a scoped
`devices: ["/dev/hidraw0:/dev/hidraw0"]` instead.

The default image and `docker-compose.yml` are USB HID only -- the `ble`
extra (see [Bluetooth LE](#bluetooth-le-optional)) isn't installed in the
`Dockerfile`, and even with it added, reaching a host's Bluetooth adapter
from inside a container needs its own passthrough (typically the D-Bus
system socket bind-mounted in, plus `network_mode: host` or explicit
Bluetooth device access) that this project doesn't set up yet. Setting
`[ble] enabled = yes` inside this container as shipped degrades the same
way it would on any host with no D-Bus reachable: logged scan-failure
warnings, not a crash, but no BLE sync either -- see BleScanLoop's
exception handling in `ble_sync.py` if debugging this.

A pre-built image publishes to GHCR from CI on every push to `main`,
tagged `latest` and by commit SHA, so `docker pull
ghcr.io/home-health-hub/trividia-truemetrix-daemon:latest` works instead of
building locally, if you'd rather not build it yourself. Substitute that
image name for `trividia-truemetrix-daemon` in the commands below to use
it instead of `docker build`.

```bash
mkdir -p config data
cp config/trividia-truemetrix-daemon.ini.example config/config.ini
"$EDITOR" config/config.ini   # set storage.db_path = /var/lib/trividia-truemetrix-daemon/readings.db
docker compose up -d --build
docker compose logs -f
```

Run any of the other console scripts inside the running container:

```bash
docker compose exec trividia-truemetrix-daemon trividia-truemetrix-report --config /etc/trividia-truemetrix-daemon/config.ini --output /var/lib/trividia-truemetrix-daemon/report.pdf
```

Without Compose, the equivalent is:

```bash
docker build -t trividia-truemetrix-daemon .
docker run -d --name trividia-truemetrix-daemon \
  --privileged \
  -v /dev:/dev \
  -v "$(pwd)/config:/etc/trividia-truemetrix-daemon" \
  -v "$(pwd)/data:/var/lib/trividia-truemetrix-daemon" \
  --restart unless-stopped \
  trividia-truemetrix-daemon
```

## Manual usage

```bash
trividia-truemetrix-daemon --config /etc/trividia-truemetrix-daemon/config.ini
trividia-truemetrix-daemon --config /etc/trividia-truemetrix-daemon/config.ini --verbose
```

Validate a config file without starting the daemon:

```bash
trividia-truemetrix-daemon --config /etc/trividia-truemetrix-daemon/config.ini --check-config
```

### On-demand capture instead of a long-running service

`--once` polls until one meter syncs (or `--once-timeout` seconds elapse)
and exits, instead of running until stopped:

```bash
trividia-truemetrix-daemon --config /etc/trividia-truemetrix-daemon/config.ini --once --once-timeout 30
```

## Reports

`trividia-truemetrix-report` reads the database and writes a table (or
chart) of readings to a PDF or CSV file. **The realistic case is one
person's report, via `--profile`** -- that's what you'd actually hand to a
doctor. Multi-meter/household reports (below) are a secondary convenience,
not the primary use case:

```bash
# One person's readings -- the report that actually matters
trividia-truemetrix-report --config /etc/trividia-truemetrix-daemon/config.ini --profile Alice --output alice-report.pdf

# Preset ranges: 7d, 30d, 90d, 1y, all (default: all)
trividia-truemetrix-report --config /etc/trividia-truemetrix-daemon/config.ini --profile Alice --period 30d --output last-30-days.pdf

# Explicit date range (--to defaults to now if omitted)
trividia-truemetrix-report --config /etc/trividia-truemetrix-daemon/config.ini --profile Alice --from 2026-01-01 --to 2026-03-31 --output q1.pdf

# Point directly at a database file instead of a config
trividia-truemetrix-report --db /var/lib/trividia-truemetrix-daemon/readings.db --device-id Trividia-BLU-12345678 --output report.pdf

# CSV instead of PDF
trividia-truemetrix-report --config /etc/trividia-truemetrix-daemon/config.ini --profile Alice --format csv --output report.csv

# Every reading on record, ignoring who owns which meter (rarely what you want)
trividia-truemetrix-report --config /etc/trividia-truemetrix-daemon/config.ini --output everything.pdf
```

Add `--device-id Trividia-BLU-12345678` to restrict the report to one
meter directly (e.g. without `--config`/profiles set up). If the database
has readings from more than one meter and you actually want a combined
household view, `--multi-meter` gives one PDF with a separate section
(its own table/chart and summary line) per meter, each starting on a fresh
page, rather than mixing every meter's readings into one table:

```bash
trividia-truemetrix-report --config /etc/trividia-truemetrix-daemon/config.ini --multi-meter --output all-meters.pdf
```

`--multi-meter` is mutually exclusive with `--device-id` and only affects
PDF output (`--format csv` ignores it, since the CSV's Device ID column
already differentiates meters in one flat file). `--profile` requires
`--config`, since profile membership lives in the config file, not the
database -- readings are matched to a profile by resolving each row's
`device_id` the same way the daemon itself does (static `[profile.<name>]`
config, falling back to dynamic onboarding assignments).

The layout, which columns appear, the unit, and the date/time format are
controlled by the `[report]` section of the config file (see the config
table above). `--db` always uses the defaults (`full` layout, mg/dL,
world date format, every optional column).

### Time in Range

Set `report.include_time_in_range = yes` for a below/in-range/above pie
chart (with counts and percentages), independent of layout:

```ini
[report]
include_time_in_range = yes
```

```bash
trividia-truemetrix-report --config /etc/trividia-truemetrix-daemon/config.ini --profile Alice --output alice.pdf
```

Unlike sliding scale, this works without `--profile` too (a plain report
uses `report.tir_low_mg_dl`/`tir_high_mg_dl`, defaulting to 70-180 mg/dL,
a common clinical target range and reasonable enough to ship as a default
-- unlike a dosing table, which can't be safely guessed). A profile's own
`tir_low_mg_dl`/`tir_high_mg_dl` overrides the global default for that
profile's report; with `--multi-meter`, each section resolves its target
band independently, the same way sliding scale does.

### Sliding scale (insulin dosing)

Set `report.include_sliding_scale = yes` and a profile's own
`[profile.<name>] sliding_scale` (see the config table above) for Dose and
Note columns, looked up per reading from that profile's bands:

```bash
trividia-truemetrix-report --config /etc/trividia-truemetrix-daemon/config.ini --profile Alice --output alice.pdf
```

> [!IMPORTANT]
> **This is a display lookup of a dosing table you already have, not
> medical advice, and not something this tool generates, validates, or
> recommends.** Populate `sliding_scale` with exactly what the person's own
> doctor prescribed for them -- sliding scales vary by insulin type,
> individual sensitivity, and treatment plan, and a table copied from
> somewhere generic (or from a different person) could be actively unsafe.
> A reading that falls outside every configured band shows "no guidance
> configured" rather than a guessed value. Overlapping bands are rejected
> at config load time, since an ambiguous table -- two bands both claiming
> to cover the same reading -- is a safety issue, not just a config nit.

With `--multi-meter`, each meter's section resolves its own profile's
sliding scale independently (see
[combined/multi-meter-sliding-scale.pdf](samples/combined/multi-meter-sliding-scale.pdf)):
a profile with no `sliding_scale` configured just shows no Dose/Note
columns for its section, with no fallback to another profile's table.

Device ID and Model are deliberately shown once in the header (a `Meter:
<device_id> (<model>)` line, right under Email) rather than as per-row
table columns in PDF output -- with Dose/Note columns already in the mix,
repeating a long device_id on every row was pushing the table off the
page edge. `report.include_device_id`/`include_model` still work, but
only affect `--format csv`, which has no page-width constraint.

See [samples/](samples/single/) for a rendered PDF of every
layout/unit/date-format combination (all single-profile, the realistic
case, in [samples/single/](samples/single/)), plus the secondary
household/combined samples in [samples/combined/](samples/combined/).

## Database schema

Each reading is inserted as one row into the `readings` table:

| Column | Type | Notes |
|---|---|---|
| `device_id` | TEXT | `Trividia-<model_code>-<serial>`, from the meter itself |
| `model` | TEXT | Full model name, e.g. `TRUE METRIX AIR` |
| `device_time` | TEXT | ISO-8601, decoded from the meter's own clock (not timezone-aware) |
| `value_mg_dl` | INTEGER | Blood glucose, mg/dL |
| `out_of_range` | TEXT | `high`, `low`, or NULL |
| `is_control_solution` | INTEGER | 1 if flagged as a control-solution test rather than a real reading |
| `raw` | TEXT | Undecoded record, used with `device_id` as the dedup key |
| `synced_at` | TEXT | ISO-8601 UTC, when this daemon inserted the row |

`UNIQUE(device_id, raw)` makes re-syncing a meter idempotent: the meter
always returns its *entire* on-device history on every `GET_RESULTS`, not
just readings added since the last sync, so without this constraint every
dock would re-insert the meter's whole history as duplicates.

Query it directly with `sqlite3`, or point any BI/graphing tool at the file.

## Acknowledgments

- Meter hardware designed and sold by [Trividia Health](https://www.trividiahealth.com)
  (see the Disclaimer above).
- Built on [`trividia-truemetrix-hid`](https://github.com/home-health-hub/trividia-truemetrix-hid),
  which does the USB HID protocol work, itself ported from Tidepool's
  open-source uploader driver.
- Daemon structure (config/storage/systemd/install.sh conventions, and the
  ntfy/dunstify notification pattern) follows
  [`etekcity-scale-daemon`](https://github.com/home-health-hub/etekcity-scale-daemon).
- Code review, implementation, and documentation assisted by [Claude](https://www.anthropic.com/claude).

## Contributing

Contributions are welcome!

- **Bug reports**: [Open an issue](https://github.com/home-health-hub/trividia-truemetrix-daemon/issues).
- **Everything else** (questions, feature requests, ideas, general discussion): [Use Discussions](https://github.com/home-health-hub/trividia-truemetrix-daemon/discussions).
- Pull requests are welcome for bug fixes or discussed features.

## License

This project is licensed under the **GNU General Public License v3.0**.

See [LICENSE](LICENSE) for more information.
