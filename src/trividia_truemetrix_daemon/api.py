"""Local HTTP API: latest readings, on-demand reports, and the ntfy assignment callback.

Reads from the same SQLite database as everything else in this package --
it's a standalone read-only view onto that data (plus /api/v1/assign-device's
one write), not part of the daemon's sync loop, so it works whether or not
the daemon is currently running.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile

from aiohttp import web

from ._version import __version__
from .assignments import AssignmentStore, resolve_profile
from .config import (
    ApiConfig,
    ConfigError,
    MqttConfig,
    ProfilesConfig,
    ReportConfig,
    load_alert_config,
    load_api_config,
    load_config,
    load_mqtt_config,
    load_onboarding_config,
    load_profiles_config,
    load_report_config,
)
from .report import (
    _resolve_range,
    build_csv,
    build_multi_meter_pdf,
    build_pdf,
    device_ids_for_profile,
    fetch_device_ids,
    fetch_rows,
)
from .storage import ensure_schema

_VALID_FORMATS = ("pdf", "csv")
_VALID_PERIODS = ("7d", "30d", "90d", "1y", "all")

_API_VERSION = "v1"
_API_PREFIX = f"/api/{_API_VERSION}"


def _latest_readings(
    db_path: str,
    profiles_config: ProfilesConfig,
    assignments: AssignmentStore,
    device_id: str | None,
    profile: str | None = None,
) -> list[dict[str, object]]:
    """Return the most recent (non-control-solution) reading for each meter.

    Args:
        db_path: Path to the SQLite database file.
        profiles_config: Supplies profile membership, for both filtering
            and the "profile" field in each result.
        assignments: Dynamic device_id -> profile assignments.
        device_id: Restrict to a single meter, if given.
        profile: Restrict to one profile's meter(s), if given.

    Returns:
        One dict per meter, each with the same fields the readings table
        stores plus a resolved "profile".
    """
    query = (
        "SELECT device_time, device_id, model, value_mg_dl, out_of_range FROM readings r1 "
        "WHERE is_control_solution = 0 AND device_time = ("
        "    SELECT MAX(device_time) FROM readings r2 "
        "    WHERE r2.device_id = r1.device_id AND r2.is_control_solution = 0"
        ")"
    )
    params: list[str] = []
    if device_id:
        query += " AND device_id = ?"
        params.append(device_id)
    elif profile:
        ids = device_ids_for_profile(profile, profiles_config, assignments)
        if not ids:
            return []
        query += f" AND device_id IN ({','.join('?' * len(ids))})"
        params.extend(ids)
    query += " ORDER BY device_id ASC"

    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute(query, params).fetchall()
    finally:
        connection.close()

    return [
        {
            "device_time": row[0],
            "device_id": row[1],
            "model": row[2],
            "value_mg_dl": row[3],
            "out_of_range": row[4],
            "profile": resolve_profile(row[1], profiles_config, assignments),
        }
        for row in rows
    ]


def _require_auth(request: web.Request) -> web.Response | None:
    token = request.app["api_token"]
    if not token:
        return None
    if request.headers.get("Authorization", "") != f"Bearer {token}":
        return web.json_response({"error": "unauthorized"}, status=401)
    return None


async def handle_health(request: web.Request) -> web.Response:
    """GET /api/v1/health -- unauthenticated liveness check."""
    return web.json_response({"status": "ok", "version": __version__})


def _capabilities(mqtt_config: MqttConfig | None) -> dict[str, object]:
    """Build the JSON body for GET /api/v1/capabilities from live config/state.

    Args:
        mqtt_config: The daemon's parsed ``[mqtt]`` section, or None if it
            couldn't be loaded (reported as simply disabled in that case).

    Returns:
        A dict describing what this daemon is and how its data is shaped,
        so a client (e.g. a Health Hub aggregator) can introspect it without
        hardcoding daemon-specific assumptions.
    """
    if mqtt_config is not None and mqtt_config.enabled:
        mqtt: dict[str, object] = {
            "enabled": True,
            "topic_pattern": f"{mqtt_config.topic_prefix}/<device_id>/state",
        }
    else:
        mqtt = {"enabled": False}

    return {
        "daemon": "trividia-truemetrix",
        "api_version": _API_VERSION,
        "measurement_types": ["glucose"],
        "measurement_modes": ["spot"],
        "profile_model": "assignable",
        "timestamp_fields": {
            "device_time": (
                "The meter's own clock at the time of the reading -- closest "
                "equivalent to \"measured at\"."
            ),
            "synced_at": (
                "When the daemon ingested the reading -- closest equivalent "
                "to \"received at\"."
            ),
        },
        "mqtt": mqtt,
    }


async def handle_capabilities(request: web.Request) -> web.Response:
    """GET /api/v1/capabilities -- unauthenticated description of what this daemon exposes."""
    return web.json_response(_capabilities(request.app.get("mqtt_config")))


async def handle_latest(request: web.Request) -> web.Response:
    """GET /api/v1/latest[?device_id=...&profile=...] -- most recent reading per meter, as JSON."""
    unauthorized = _require_auth(request)
    if unauthorized is not None:
        return unauthorized

    readings = _latest_readings(
        request.app["db_path"],
        request.app["profiles_config"],
        request.app["assignments"],
        request.query.get("device_id"),
        request.query.get("profile"),
    )
    if not readings:
        return web.json_response({"error": "no readings found"}, status=404)
    return web.json_response(readings)


async def handle_assign_device(request: web.Request) -> web.Response:
    """GET/POST /api/v1/assign-device?device_id=...&profile=... -- bind a meter to a profile.

    Accepts GET too, since ntfy's http action type is simplest to configure
    as a bare URL hit rather than a POST with a body.
    """
    unauthorized = _require_auth(request)
    if unauthorized is not None:
        return unauthorized

    device_id = request.query.get("device_id", "")
    if not device_id:
        return web.json_response({"error": "device_id is required"}, status=400)

    profile = request.query.get("profile", "")
    assignments: AssignmentStore = request.app["assignments"]
    known_names = request.app["profile_names"]
    if profile not in known_names:
        return web.json_response({"error": f"profile must be one of {known_names}"}, status=400)

    assignments.set(device_id, profile)
    return web.json_response({"status": "ok", "device_id": device_id, "profile": profile})


async def handle_report(request: web.Request) -> web.Response:
    """GET /api/v1/report -- format/period/from/to/device_id/profile/multi_meter query params.

    Generates a report on demand using the same config-driven settings as
    trividia-truemetrix-report and returns it as a file download.
    """
    unauthorized = _require_auth(request)
    if unauthorized is not None:
        return unauthorized

    fmt = request.query.get("format", "pdf")
    if fmt not in _VALID_FORMATS:
        return web.json_response({"error": f"format must be one of {_VALID_FORMATS}"}, status=400)

    period = request.query.get("period", "all")
    if period not in _VALID_PERIODS:
        return web.json_response(
            {"error": f"period must be one of {_VALID_PERIODS}"}, status=400
        )

    try:
        start, end = _resolve_range(period, request.query.get("from"), request.query.get("to"))
    except ValueError as exc:
        return web.json_response({"error": f"invalid date: {exc}"}, status=400)

    device_id = request.query.get("device_id")
    profile = request.query.get("profile")
    multi_meter = request.query.get("multi_meter") == "1"
    if multi_meter and device_id:
        return web.json_response(
            {"error": "multi_meter and device_id are mutually exclusive"}, status=400
        )

    db_path = request.app["db_path"]
    report_config: ReportConfig = request.app["report_config"]
    profiles_config: ProfilesConfig = request.app["profiles_config"]
    assignments: AssignmentStore = request.app["assignments"]

    sliding_scale: tuple = ()
    profile_obj = None
    if profile and not multi_meter:
        if profile not in profiles_config.profiles:
            return web.json_response(
                {"error": f"no [profile.{profile}] section in the config"}, status=400
            )
        profile_obj = profiles_config.profiles[profile]
        sliding_scale = profile_obj.sliding_scale
    elif report_config.include_sliding_scale and not multi_meter:
        return web.json_response(
            {
                "error": (
                    "report.include_sliding_scale is enabled but no ?profile= was "
                    "given -- the dosing table comes from that profile's "
                    "[profile.<name>] section"
                )
            },
            status=400,
        )

    fd, temp_path = tempfile.mkstemp(suffix=f".{fmt}")
    os.close(fd)
    try:
        if multi_meter and fmt != "csv":
            device_ids = fetch_device_ids(db_path, start, end)
            sections = [
                (d, fetch_rows(db_path, profiles_config, assignments, d, start, end))
                for d in device_ids
            ]
            sections = [(d, rows) for d, rows in sections if rows]
            if not sections:
                return web.json_response(
                    {"error": "no readings found for the given range/filters"}, status=404
                )
            build_multi_meter_pdf(sections, temp_path, report_config, profiles_config)
            content_type = "application/pdf"
        else:
            rows = fetch_rows(db_path, profiles_config, assignments, device_id, start, end, profile)
            if not rows:
                return web.json_response(
                    {"error": "no readings found for the given range/filters"}, status=404
                )
            if fmt == "csv":
                build_csv(rows, temp_path, report_config, sliding_scale)
                content_type = "text/csv"
            else:
                build_pdf(rows, temp_path, report_config, sliding_scale, profile_obj)
                content_type = "application/pdf"
        with open(temp_path, "rb") as report_file:
            body = report_file.read()
    finally:
        os.remove(temp_path)

    return web.Response(
        body=body,
        content_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="readings-report.{fmt}"'},
    )


def build_app(
    db_path: str,
    assignments: AssignmentStore,
    api_config: ApiConfig,
    profiles_config: ProfilesConfig,
    report_config: ReportConfig,
    mqtt_config: MqttConfig | None = None,
) -> web.Application:
    app = web.Application()
    app["db_path"] = db_path
    app["assignments"] = assignments
    app["api_token"] = api_config.token
    app["profile_names"] = list(profiles_config.profiles.keys())
    app["profiles_config"] = profiles_config
    app["report_config"] = report_config
    app["mqtt_config"] = mqtt_config
    app.router.add_get(f"{_API_PREFIX}/health", handle_health)
    app.router.add_get(f"{_API_PREFIX}/capabilities", handle_capabilities)
    app.router.add_get(f"{_API_PREFIX}/latest", handle_latest)
    app.router.add_get(f"{_API_PREFIX}/report", handle_report)
    app.router.add_get(f"{_API_PREFIX}/assign-device", handle_assign_device)
    app.router.add_post(f"{_API_PREFIX}/assign-device", handle_assign_device)
    return app


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="trividia-truemetrix-api",
        description="Local HTTP API: latest readings, on-demand reports, and the "
        "device-assignment callback.",
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
        api_config = load_api_config(args.config)
        onboarding_config = load_onboarding_config(args.config)
        profiles_config = load_profiles_config(args.config)
        report_config = load_report_config(args.config)
        mqtt_config = load_mqtt_config(args.config)
        # Loaded (and thus validated) even though unused here, so a config
        # error in [alerting] is caught at API startup too.
        load_alert_config(args.config)
    except ConfigError as exc:
        print(f"Error: {exc}")
        return 1

    if not api_config.enabled:
        print("API is disabled (api.enabled = no).")
        return 0

    ensure_schema(db_path)
    assignments = AssignmentStore(onboarding_config.assignments_path)
    app = build_app(db_path, assignments, api_config, profiles_config, report_config, mqtt_config)
    print(f"Listening on http://{api_config.host}:{api_config.port}")
    web.run_app(app, host=api_config.host, port=api_config.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
