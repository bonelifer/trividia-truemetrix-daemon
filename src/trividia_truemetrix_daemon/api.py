"""Minimal local HTTP API: just enough for ntfy's assignment action buttons to call back into.

Unlike etekcity-scale-daemon's API, there's no /latest or /report here yet
-- this exists solely to receive the "assign this device to that profile"
callback described in onboarding.py/notify.py. See that pair for the
matching dunstify path used when this API is disabled.
"""

from __future__ import annotations

import argparse

from aiohttp import web

from ._version import __version__
from .assignments import AssignmentStore
from .config import (
    ApiConfig,
    ConfigError,
    load_api_config,
    load_config,
    load_onboarding_config,
    load_profiles_config,
)


def _require_auth(request: web.Request) -> web.Response | None:
    token = request.app["api_token"]
    if not token:
        return None
    if request.headers.get("Authorization", "") != f"Bearer {token}":
        return web.json_response({"error": "unauthorized"}, status=401)
    return None


async def handle_health(request: web.Request) -> web.Response:
    """GET /health -- unauthenticated liveness check."""
    return web.json_response({"status": "ok", "version": __version__})


async def handle_assign_device(request: web.Request) -> web.Response:
    """GET/POST /assign-device?device_id=...&profile=... -- bind a meter to a profile.

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
    valid_profiles: AssignmentStore = request.app["assignments"]
    known_names = request.app["profile_names"]
    if profile not in known_names:
        return web.json_response({"error": f"profile must be one of {known_names}"}, status=400)

    valid_profiles.set(device_id, profile)
    return web.json_response({"status": "ok", "device_id": device_id, "profile": profile})


def build_app(
    assignments: AssignmentStore, api_config: ApiConfig, profile_names: list[str]
) -> web.Application:
    app = web.Application()
    app["assignments"] = assignments
    app["api_token"] = api_config.token
    app["profile_names"] = profile_names
    app.router.add_get("/health", handle_health)
    app.router.add_get("/assign-device", handle_assign_device)
    app.router.add_post("/assign-device", handle_assign_device)
    return app


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="trividia-truemetrix-api",
        description="Minimal local HTTP API for the device-assignment callback.",
    )
    parser.add_argument(
        "-c", "--config", required=True, help="Path to the daemon's INI config file"
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        # Loaded (and thus validated) even though db_path itself isn't used
        # here, so a config error in [daemon]/[storage] is caught at
        # startup rather than only when the main daemon happens to load it.
        load_config(args.config)
        api_config = load_api_config(args.config)
        onboarding_config = load_onboarding_config(args.config)
        profiles_config = load_profiles_config(args.config)
    except ConfigError as exc:
        print(f"Error: {exc}")
        return 1

    if not api_config.enabled:
        print("API is disabled (api.enabled = no).")
        return 0

    assignments = AssignmentStore(onboarding_config.assignments_path)
    app = build_app(assignments, api_config, list(profiles_config.profiles.keys()))
    print(f"Listening on http://{api_config.host}:{api_config.port}")
    web.run_app(app, host=api_config.host, port=api_config.port, print=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
