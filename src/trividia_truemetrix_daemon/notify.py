"""New-device onboarding notifications: interactive assignment prompt + admin heads-up.

Two things fire together, once per newly-seen device_id (throttling lives in
onboarding.py, not here):

- An interactive prompt asking which profile owns the device: ntfy (with one
  HTTP action button per profile, calling back into the local API) if the
  API is reachable, otherwise a local ``dunstify`` prompt that resolves
  synchronously and writes the assignment directly. Mirrors
  etekcity-scale-daemon's profile-assignment notification exactly.
- An unconditional, non-interactive Apprise notification to every configured
  admin URL, so an admin is informed even if nobody sees or acts on the
  interactive prompt.
"""

from __future__ import annotations

import asyncio
import logging

import aiohttp
from apprise import Apprise

from .assignments import AssignmentStore
from .config import ApiConfig, OnboardingConfig

_LOGGER = logging.getLogger(__name__)

_NTFY_REQUEST_TIMEOUT_SECONDS = 10
_NTFY_RETRY_DELAYS_SECONDS = (1, 2)
#: Worst-case time a retrying ntfy publish can take -- used to size the
#: daemon's shutdown wait for a still-retrying notification.
NTFY_MAX_RETRY_SECONDS = _NTFY_REQUEST_TIMEOUT_SECONDS * (
    len(_NTFY_RETRY_DELAYS_SECONDS) + 1
) + sum(_NTFY_RETRY_DELAYS_SECONDS)


async def notify_via_ntfy(
    device_id: str, model: str, onboarding_config: OnboardingConfig, profile_names: list[str]
) -> None:
    """Announce an unassigned device via ntfy, with one action button per profile.

    Retries up to twice (after 1s, then 2s) on a connection failure or a 5xx
    response, matching etekcity-scale-daemon's profile-notification retry
    policy -- a brief outage at exactly the wrong moment shouldn't mean the
    device can only ever be assigned via --find-unassigned/the API directly.
    """
    callback_base = f"{onboarding_config.api_base_url}/assign-device"
    headers = {}
    if onboarding_config.ntfy_token:
        headers["Authorization"] = f"Bearer {onboarding_config.ntfy_token}"

    clean_url = onboarding_config.ntfy_url.rstrip("/")
    ntfy_root, _, topic = clean_url.rpartition("/")
    if not ntfy_root:
        ntfy_root = clean_url

    payload = {
        "topic": topic,
        "title": "New TRUE METRIX meter",
        "message": f"{model} ({device_id}) synced -- who does this belong to?",
        "actions": [
            {
                "action": "http",
                "label": name,
                "url": f"{callback_base}?device_id={device_id}&profile={name}",
                "method": "POST",
                "clear": True,
            }
            for name in profile_names
        ],
    }

    last_error = None
    for attempt in range(len(_NTFY_RETRY_DELAYS_SECONDS) + 1):
        if attempt > 0:
            await asyncio.sleep(_NTFY_RETRY_DELAYS_SECONDS[attempt - 1])
        try:
            timeout = aiohttp.ClientTimeout(total=_NTFY_REQUEST_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(ntfy_root, json=payload, headers=headers) as response:
                    if response.status >= 500:
                        last_error = f"HTTP {response.status}: {await response.text()}"
                        continue
                    if response.status >= 400:
                        _LOGGER.warning(
                            "ntfy publish failed with HTTP %s: %s",
                            response.status,
                            await response.text(),
                        )
                    return
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_error = str(exc) or repr(exc)
            continue

    _LOGGER.warning(
        "ntfy publish failed after %d attempt(s): %s",
        len(_NTFY_RETRY_DELAYS_SECONDS) + 1,
        last_error,
    )


async def prompt_via_dunstify(
    device_id: str,
    model: str,
    onboarding_config: OnboardingConfig,
    profile_names: list[str],
    assignments: AssignmentStore,
) -> None:
    """Ask locally (via dunstify) which profile an unassigned device belongs to.

    Resolves synchronously and writes the assignment directly -- there's no
    HTTP API to call back into in this path.
    """
    args = ["dunstify", "-t", str(onboarding_config.dunstify_timeout_seconds * 1000)]
    for name in profile_names:
        args += ["--action", f"{name},{name}"]
    args += ["New TRUE METRIX meter", f"{model} ({device_id}) synced -- who does this belong to?"]

    try:
        process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        stdout, _ = await asyncio.wait_for(
            process.communicate(), timeout=onboarding_config.dunstify_timeout_seconds + 5
        )
    except (OSError, asyncio.TimeoutError) as exc:
        _LOGGER.warning("dunstify assignment prompt failed: %s", exc)
        return

    chosen = stdout.decode().strip()
    if chosen not in profile_names:
        _LOGGER.info("No profile chosen for device %s (timed out or dismissed)", device_id)
        return

    assignments.set(device_id, chosen)
    _LOGGER.info("Assigned device %s to profile %s", device_id, chosen)


async def prompt_for_assignment(
    device_id: str,
    model: str,
    onboarding_config: OnboardingConfig,
    profile_names: list[str],
    assignments: AssignmentStore,
    api_config: ApiConfig,
) -> None:
    """Dispatch to ntfy (if the API is reachable) or dunstify (if not)."""
    if not profile_names:
        _LOGGER.info(
            "Device %s is unassigned but no profiles are configured; skipping prompt", device_id
        )
        return
    if api_config.enabled:
        await notify_via_ntfy(device_id, model, onboarding_config, profile_names)
    else:
        await prompt_via_dunstify(device_id, model, onboarding_config, profile_names, assignments)


async def notify_admin(device_id: str, model: str, onboarding_config: OnboardingConfig) -> None:
    """Send an unconditional, non-interactive heads-up to every admin Apprise target.

    Fires regardless of whether the interactive prompt succeeds, is seen, or
    is acted on -- this is the "in case the user doesn't see it" channel.
    Apprise's own senders are synchronous, so this runs in a worker thread.
    """
    if not onboarding_config.admin_apprise_urls:
        return

    apprise = Apprise()
    for url in onboarding_config.admin_apprise_urls:
        apprise.add(url)

    title = "trividia-truemetrix-daemon: unassigned device"
    body = (
        f"A new TRUE METRIX meter ({model}, device_id={device_id}) was synced "
        "and isn't assigned to a profile yet. An interactive assignment prompt "
        "was also sent, but this notification fires regardless of whether "
        "that one is seen or acted on."
    )
    try:
        await asyncio.to_thread(apprise.notify, title=title, body=body)
    except Exception:
        _LOGGER.exception("Admin Apprise notification failed for device %s", device_id)
