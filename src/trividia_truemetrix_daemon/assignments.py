"""Dynamic device_id -> profile assignments, written by the onboarding prompt.

Static ``[profile.<name>]`` config entries are the deliberate, permanent
bindings (see config.py) and always take precedence. This file only holds
assignments made by tapping a notification action button for a device_id
that wasn't statically claimed -- kept separate from the human-edited INI
file so a background process never rewrites it.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import ProfilesConfig


class AssignmentStore:
    """A JSON-backed device_id -> profile name map."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def _load(self) -> dict[str, str]:
        if not self._path.is_file():
            return {}
        return json.loads(self._path.read_text())

    def get(self, device_id: str) -> str | None:
        return self._load().get(device_id)

    def set(self, device_id: str, profile_name: str) -> None:
        """Record device_id -> profile_name, overwriting any prior dynamic assignment.

        A deliberate re-tap (e.g. a meter changing hands) is treated as an
        intentional override, unlike a static config file listing the same
        device_id under two profiles, which is a hard error -- see
        config.load_profiles_config.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        assignments = self._load()
        assignments[device_id] = profile_name
        self._path.write_text(json.dumps(assignments, indent=2, sort_keys=True))

    def all(self) -> dict[str, str]:
        return self._load()


def resolve_profile(
    device_id: str, profiles_config: ProfilesConfig, assignments: AssignmentStore
) -> str | None:
    """Return the profile name owning device_id, static config taking priority."""
    static_owner = profiles_config.device_id_owner(device_id)
    if static_owner is not None:
        return static_owner
    return assignments.get(device_id)
