#!/usr/bin/env python3
"""List device_ids present in the database with no profile assignment.

The durable fallback for reconciling any device the onboarding prompt
(ntfy/dunstify) and the admin Apprise notification were both missed for --
see onboarding.py.
"""

from __future__ import annotations

import argparse

from ._version import __version__
from .assignments import AssignmentStore, resolve_profile
from .config import ConfigError, load_config, load_onboarding_config, load_profiles_config
from .storage import get_known_device_ids


def find_unassigned(config_path: str) -> list[str]:
    """Return every device_id with stored readings but no profile, sorted."""
    daemon_config = load_config(config_path)
    profiles_config = load_profiles_config(config_path)
    onboarding_config = load_onboarding_config(config_path)
    assignments = AssignmentStore(onboarding_config.assignments_path)

    known = get_known_device_ids(daemon_config.db_path)
    return sorted(
        device_id
        for device_id in known
        if resolve_profile(device_id, profiles_config, assignments) is None
    )


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-c", "--config", required=True, help="path to the INI config file")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    try:
        unassigned = find_unassigned(args.config)
    except ConfigError as exc:
        print(f"Error: {exc}")
        raise SystemExit(1) from exc

    if not unassigned:
        print("No unassigned devices.")
        return
    for device_id in unassigned:
        print(device_id)


if __name__ == "__main__":
    main()
