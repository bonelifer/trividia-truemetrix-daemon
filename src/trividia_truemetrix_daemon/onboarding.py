"""Throttled orchestration for the new-device onboarding notification.

Fires the interactive assignment prompt and the unconditional admin
notification together, exactly once per newly-seen unassigned device_id --
see notify.py for what each channel actually sends.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
from pathlib import Path

from .assignments import AssignmentStore, resolve_profile
from .config import ApiConfig, OnboardingConfig, ProfilesConfig
from .notify import notify_admin, prompt_for_assignment

_LOGGER = logging.getLogger(__name__)


class OnboardingState:
    """Tracks which device_ids have already triggered the onboarding prompt."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def _load(self) -> dict[str, str]:
        if not self._path.is_file():
            return {}
        return json.loads(self._path.read_text())

    def already_prompted(self, device_id: str) -> bool:
        return device_id in self._load()

    def mark_prompted(self, device_id: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        state = self._load()
        state[device_id] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self._path.write_text(json.dumps(state, indent=2, sort_keys=True))


async def check_device(
    device_id: str,
    model: str,
    onboarding_config: OnboardingConfig,
    profiles_config: ProfilesConfig,
    assignments: AssignmentStore,
    api_config: ApiConfig,
) -> None:
    """Fire the onboarding notification if device_id is unassigned and unseen.

    No-op if onboarding is disabled, the device already has a profile
    (static or dynamic), or this device_id has already triggered a prompt
    before -- the prompt fires once per device_id, ever; anything missed by
    it is meant to be found via the find-unassigned CLI tool instead of
    being re-notified.
    """
    if not onboarding_config.enabled:
        return
    if resolve_profile(device_id, profiles_config, assignments) is not None:
        return

    state = OnboardingState(onboarding_config.state_path)
    if state.already_prompted(device_id):
        return

    _LOGGER.info("Unassigned device %s (%s) seen for the first time", device_id, model)
    profile_names = list(profiles_config.profiles.keys())
    await asyncio.gather(
        prompt_for_assignment(
            device_id, model, onboarding_config, profile_names, assignments, api_config
        ),
        notify_admin(device_id, model, onboarding_config),
    )
    state.mark_prompted(device_id)
