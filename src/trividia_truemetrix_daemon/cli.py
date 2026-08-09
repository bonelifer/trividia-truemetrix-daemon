#!/usr/bin/env python3
"""Daemon entry point: polls for docked TRUE METRIX meters and syncs readings."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from pathlib import Path

from ._version import __version__
from .assignments import AssignmentStore
from .config import (
    ApiConfig,
    ConfigError,
    DEFAULT_API_CONFIG,
    DEFAULT_ONBOARDING_CONFIG,
    DEFAULT_PROFILES_CONFIG,
    OnboardingConfig,
    ProfilesConfig,
    load_api_config,
    load_config,
    load_onboarding_config,
    load_profiles_config,
)
from .storage import ReadingStore
from .sync import PollLoop

_LOGGER = logging.getLogger("trividia_truemetrix_daemon")


async def run_daemon(
    config_path: str,
    poll_interval_seconds: float,
    db_path: str,
    onboarding_config: OnboardingConfig = DEFAULT_ONBOARDING_CONFIG,
    profiles_config: ProfilesConfig = DEFAULT_PROFILES_CONFIG,
    api_config: ApiConfig = DEFAULT_API_CONFIG,
    once: bool = False,
    once_timeout: float = 60.0,
) -> bool:
    """Run the poll loop until a stop signal (or, with once=True, one sync).

    Returns:
        True if at least one meter was synced. Always True for a normal
        (non-once) run, which only returns via a stop signal.
    """
    store = ReadingStore(db_path)
    assignments = AssignmentStore(onboarding_config.assignments_path)
    loop = PollLoop(
        store, onboarding_config, profiles_config, assignments, api_config, poll_interval_seconds
    )

    _LOGGER.info(
        "Starting trividia-truemetrix-daemon %s (config=%s%s)",
        __version__,
        config_path,
        f", once, {once_timeout}s timeout" if once else "",
    )

    try:
        if once:
            synced = await loop.run_once(once_timeout)
            if not synced:
                _LOGGER.warning("No meter synced within %s seconds", once_timeout)
            return synced

        stop_event = asyncio.Event()
        running_loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            running_loop.add_signal_handler(sig, stop_event.set)
        await loop.run(stop_event)
        return True
    finally:
        store.close()


def _check_config(config_path: str) -> int:
    """Validate a config file against every section loader, without running."""
    if not Path(config_path).is_file():
        print(f"Error: Config file not found: {config_path}")
        return 1

    errors: list[str] = []
    daemon_config = onboarding_config = api_config = profiles_config = None

    try:
        daemon_config = load_config(config_path)
    except ConfigError as exc:
        errors.append(str(exc))
    try:
        profiles_config = load_profiles_config(config_path)
    except ConfigError as exc:
        errors.append(str(exc))
    try:
        onboarding_config = load_onboarding_config(config_path)
    except ConfigError as exc:
        errors.append(str(exc))
    try:
        api_config = load_api_config(config_path)
    except ConfigError as exc:
        errors.append(str(exc))

    if errors:
        print("Config errors:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("Config OK:")
    print(f"  storage.db_path = {daemon_config.db_path}")
    print(f"  daemon.poll_interval_seconds = {daemon_config.poll_interval_seconds}")
    print(f"  profiles: {list(profiles_config.profiles.keys()) or '(none configured)'}")
    print(f"  onboarding.enabled = {onboarding_config.enabled}")
    if onboarding_config.enabled and not profiles_config.profiles:
        print(
            "  warning: onboarding.enabled = yes but no [profile.<name>] sections exist "
            "-- unassigned devices will only ever notify the admin, never prompt for assignment"
        )
    print(f"  api.enabled = {api_config.enabled}")
    if onboarding_config.enabled and not api_config.enabled:
        print("  note: api.enabled = no -- assignment prompts will use dunstify, not ntfy")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-c", "--config", required=True, help="path to the INI config file")
    parser.add_argument(
        "-1", "--once", action="store_true",
        help="sync once and exit, instead of running until stopped",
    )
    parser.add_argument(
        "-t", "--once-timeout", type=float, default=60.0,
        help="seconds to wait for a meter to dock, with --once (default: 60)",
    )
    parser.add_argument(
        "--check-config", action="store_true", help="validate the config file and exit"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="enable debug logging")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    if args.check_config:
        raise SystemExit(_check_config(args.config))

    try:
        daemon_config = load_config(args.config)
        profiles_config = load_profiles_config(args.config)
        onboarding_config = load_onboarding_config(args.config)
        api_config = load_api_config(args.config)
    except ConfigError as exc:
        print(f"Error: {exc}")
        raise SystemExit(1) from exc

    try:
        asyncio.run(
            run_daemon(
                args.config,
                daemon_config.poll_interval_seconds,
                daemon_config.db_path,
                onboarding_config,
                profiles_config,
                api_config,
                once=args.once,
                once_timeout=args.once_timeout,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
