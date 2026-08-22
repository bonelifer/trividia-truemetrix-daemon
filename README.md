# trividia-truemetrix-daemon

![trividia-truemetrix-daemon: glucose readings over Bluetooth or USB to a local home server and database](docs/images/trividia-truemetrix-daemon-banner.png)

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white) ![Bash](https://img.shields.io/badge/shell-Bash-4EAA25?logo=gnu-bash&logoColor=white) ![Bluetooth LE](https://img.shields.io/badge/Bluetooth-LE-0082FC?logo=bluetooth&logoColor=white) ![USB HID](https://img.shields.io/badge/USB-HID-FF7A61?logo=usb&logoColor=white)

[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue)](https://github.com/home-health-hub/trividia-truemetrix-daemon/blob/main/LICENSE) [![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/home-health-hub/trividia-truemetrix-daemon#contributing) [![Discussions](https://img.shields.io/badge/discussions-welcome-blue)](https://github.com/home-health-hub/trividia-truemetrix-daemon/discussions)

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

See the [Installation wiki page](https://github.com/home-health-hub/trividia-truemetrix-daemon/wiki/Installation)
for the full key-by-key configuration reference (profiles, sliding scale,
thresholds, BLE, onboarding, API, report, and MQTT sections) and the
manual (non-`install.sh`) systemd setup steps.

## Bluetooth LE (optional)

TRUE METRIX AIR can also sync directly over Bluetooth LE, no docking
station needed, running alongside the USB HID poll loop rather than
replacing it -- off by default. See the
[Bluetooth LE wiki page](https://github.com/home-health-hub/trividia-truemetrix-daemon/wiki/Bluetooth-LE)
for the extra install step, config, `device_id` format, and current
verification status.

## Onboarding new devices

When an unrecognized meter's `device_id` first syncs, an interactive
assignment prompt (ntfy or a local `dunstify` prompt) and an unconditional
admin notification both fire once, then go quiet for that device;
`trividia-truemetrix-find-unassigned` is a durable fallback for anything
missed. See the
[Onboarding wiki page](https://github.com/home-health-hub/trividia-truemetrix-daemon/wiki/Onboarding)
for enabling it and the full assignment flow.

## Alerting

Optional and off by default: `trividia-truemetrix-alert-check` notifies
via [Apprise](https://github.com/caronc/apprise) when a meter's latest
reading crosses a high/low threshold, or when a meter goes stale. See the
[Alerting wiki page](https://github.com/home-health-hub/trividia-truemetrix-daemon/wiki/Alerting)
for configuration and the bundled systemd timer.

## HTTP API

Optional and off by default: `trividia-truemetrix-api` runs a small local,
read-only HTTP server (`/api/v1/latest`, on-demand `/api/v1/report`,
device assignment) for fetching data without shelling in. See the
[HTTP API wiki page](https://github.com/home-health-hub/trividia-truemetrix-daemon/wiki/HTTP-API)
for the full endpoint reference, authentication, and the capabilities
endpoint.

## MQTT

Optional and off by default: publishes each newly-synced reading to an
MQTT broker as JSON, alongside the local SQLite recording. See the
[MQTT wiki page](https://github.com/home-health-hub/trividia-truemetrix-daemon/wiki/MQTT)
for the full config and topic/payload reference.

## Pruning old data

`trividia-truemetrix-prune` manually deletes readings older than a given
number of days; it's a dry run by default until you pass `--yes`. See the
[Pruning wiki page](https://github.com/home-health-hub/trividia-truemetrix-daemon/wiki/Pruning)
for the full flag reference.

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
doctor:

```bash
trividia-truemetrix-report --config /etc/trividia-truemetrix-daemon/config.ini --profile Alice --output alice-report.pdf
```

See the [Reports wiki page](https://github.com/home-health-hub/trividia-truemetrix-daemon/wiki/Generating-Reports)
for every flag, preset/explicit date ranges, `--multi-meter` household
reports, Time in Range, the sliding-scale dosing display, and sample PDFs.

## Database schema

Each reading is stored as one row in the `readings` table, keyed so
re-docking the same meter is always safe (the meter returns its entire
history every sync, not just new readings). See the
[Database schema wiki page](https://github.com/home-health-hub/trividia-truemetrix-daemon/wiki/Database-Schema)
for the full column reference.

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
