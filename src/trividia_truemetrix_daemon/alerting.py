"""Check for stale or out-of-threshold readings and notify via Apprise.

device_time (the meter's own clock) is naive -- no timezone concept, see
storage.py -- so every comparison here uses naive datetimes too, "now"
included, rather than attaching a false UTC tzinfo to the meter's clock.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import apprise

from ._version import __version__
from .assignments import AssignmentStore, resolve_profile
from .config import (
    AlertConfig,
    ConfigError,
    ProfilesConfig,
    load_alert_config,
    load_config,
    load_onboarding_config,
    load_profiles_config,
)

# Minimum time between repeat staleness alerts for the same meter, so a
# once-hourly check doesn't re-notify every single run while data stays old.
_STALE_ALERT_THROTTLE = timedelta(days=1)


def _load_state(state_path: str) -> dict[str, dict[str, str]]:
    """Load per-device_id alert state, tolerating a missing or corrupt file."""
    path = Path(state_path)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state_path: str, state: dict[str, dict[str, str]]) -> None:
    """Persist per-device_id alert state, creating the parent directory if needed."""
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2))


def _all_device_ids(db_path: str) -> list[str]:
    """Return every distinct device_id with at least one real (non-control-solution) reading."""
    connection = sqlite3.connect(db_path)
    try:
        return [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT device_id FROM readings WHERE is_control_solution = 0"
            ).fetchall()
        ]
    finally:
        connection.close()


def _latest_reading(db_path: str, device_id: str) -> tuple[str, int] | None:
    """Return (device_time, value_mg_dl) for device_id's most recent real reading, if any."""
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT device_time, value_mg_dl FROM readings "
            "WHERE device_id = ? AND is_control_solution = 0 "
            "ORDER BY device_time DESC LIMIT 1",
            (device_id,),
        ).fetchone()
        return tuple(row) if row is not None else None
    finally:
        connection.close()


def _effective_thresholds(
    device_id: str,
    alert_config: AlertConfig,
    profiles_config: ProfilesConfig,
    assignments: AssignmentStore,
) -> tuple[int, int]:
    """Resolve (high, low) thresholds for device_id: profile override, else global default."""
    profile_id = resolve_profile(device_id, profiles_config, assignments)
    high, low = alert_config.high_threshold_mg_dl, alert_config.low_threshold_mg_dl
    if profile_id is not None and profile_id in profiles_config.profiles:
        profile = profiles_config.profiles[profile_id]
        if profile.high_threshold_mg_dl is not None:
            high = profile.high_threshold_mg_dl
        if profile.low_threshold_mg_dl is not None:
            low = profile.low_threshold_mg_dl
    return high, low


def check_alerts(
    db_path: str,
    alert_config: AlertConfig,
    profiles_config: ProfilesConfig,
    assignments: AssignmentStore,
    now: datetime | None = None,
) -> list[str]:
    """Evaluate staleness and high/low threshold conditions for every known meter.

    A high/low alert only fires the first time a given "latest reading" is
    seen, so it isn't repeated on every subsequent run until a new reading
    arrives. A staleness alert repeats at most once per
    ``_STALE_ALERT_THROTTLE`` while the condition persists.

    Args:
        db_path: Path to the SQLite database file.
        alert_config: Parsed [alerting] configuration.
        profiles_config: Supplies per-profile threshold overrides.
        assignments: Dynamic device_id -> profile assignments, for
            resolving profile overrides on devices not statically claimed.
        now: Current time, naive (matching device_time -- see the module
            docstring's timezone note); injectable for testing. Defaults to
            UTC now with tzinfo stripped, same approximation report.py uses
            elsewhere for comparing against the meter's own naive clock.

    Returns:
        Triggered alert messages (empty if nothing was triggered). The
        caller is responsible for actually sending them.
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    state = _load_state(alert_config.state_path)
    messages: list[str] = []

    for device_id in _all_device_ids(db_path):
        device_state = state.get(device_id, {})
        latest = _latest_reading(db_path, device_id)
        if latest is None:
            continue
        latest_device_time, latest_value = latest
        latest_dt = datetime.fromisoformat(latest_device_time)

        if alert_config.stale_after_days > 0:
            if now - latest_dt > timedelta(days=alert_config.stale_after_days):
                last_alert = device_state.get("last_stale_alert_at")
                last_alert_dt = datetime.fromisoformat(last_alert) if last_alert else None
                if last_alert_dt is None or now - last_alert_dt > _STALE_ALERT_THROTTLE:
                    messages.append(
                        f"No reading from {device_id} in over "
                        f"{alert_config.stale_after_days} day(s) "
                        f"(last: {latest_device_time})"
                    )
                    device_state["last_stale_alert_at"] = now.isoformat()
            else:
                device_state.pop("last_stale_alert_at", None)

        high, low = _effective_thresholds(device_id, alert_config, profiles_config, assignments)
        already_seen = device_state.get("last_seen_device_time") == latest_device_time
        if not already_seen:
            if high > 0 and latest_value > high:
                messages.append(
                    f"Glucose for {device_id} is {latest_value} mg/dL, above the "
                    f"{high} mg/dL threshold (at {latest_device_time})"
                )
            elif low > 0 and latest_value < low:
                messages.append(
                    f"Glucose for {device_id} is {latest_value} mg/dL, below the "
                    f"{low} mg/dL threshold (at {latest_device_time})"
                )

        device_state["last_seen_device_time"] = latest_device_time
        state[device_id] = device_state

    _save_state(alert_config.state_path, state)
    return messages


def send_alerts(apprise_urls: list[str], messages: list[str]) -> None:
    """Send each message via Apprise to every configured notification URL."""
    notifier = apprise.Apprise()
    for url in apprise_urls:
        notifier.add(url)
    for message in messages:
        notifier.notify(title="TRUE METRIX Glucose Alert", body=message)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="trividia-truemetrix-alert-check",
        description="Check for stale or out-of-threshold readings and notify via Apprise.",
    )
    parser.add_argument(
        "-c", "--config", required=True, help="Path to the daemon's INI config file"
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        db_path = load_config(args.config).db_path
        alert_config = load_alert_config(args.config)
        profiles_config = load_profiles_config(args.config)
        assignments_path = load_onboarding_config(args.config).assignments_path
    except ConfigError as exc:
        print(f"Error: {exc}")
        return 1

    if not alert_config.enabled:
        print("Alerting is disabled (alerting.enabled = no).")
        return 0

    assignments = AssignmentStore(assignments_path)
    messages = check_alerts(db_path, alert_config, profiles_config, assignments)
    if not messages:
        print("No alerts triggered.")
        return 0

    send_alerts(alert_config.apprise_urls, messages)
    for message in messages:
        print(f"ALERT: {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
