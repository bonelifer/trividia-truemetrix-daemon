"""Configuration loading and validation for the daemon."""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

from .dosing import SlidingScaleBand, SlidingScaleError, parse_sliding_scale


class ConfigError(Exception):
    """Raised when the configuration file is missing or invalid."""


@dataclass
class DaemonConfig:
    """Parsed ``[daemon]``/``[storage]`` sections."""

    config_path: Path
    poll_interval_seconds: float
    db_path: str
    log_level: str


@dataclass
class ProfileConfig:
    """One ``[profile.<name>]`` section: a person and the meter(s) they own.

    Unlike etekcity-scale-daemon's profiles (runtime "who was this?"
    tagging of a shared device), a TRUE METRIX meter has its own serial
    number, so ownership is declared statically here rather than asked at
    read time -- see device_ids.

    The profile's id -- the ``<name>`` in ``[profile.<name>]`` -- is used
    everywhere identity matters: the ntfy/dunstify action button label, the
    ``?profile=`` value the API and assignments store expect, and the
    ``ProfilesConfig.profiles`` dict key. full_name is separate: purely
    for display (e.g. a future report header), defaulting to the id itself
    if left blank, and never used for matching.

    sliding_scale is a display convenience for a dosing table this
    person's doctor already gave them -- see dosing.py's module docstring
    for what it is and, more importantly, what it deliberately is not.
    """

    full_name: str
    email: str
    notes: str
    device_ids: tuple[str, ...]
    sliding_scale: tuple[SlidingScaleBand, ...]


@dataclass
class ProfilesConfig:
    """Every configured profile, keyed by id (see ProfileConfig)."""

    profiles: dict[str, ProfileConfig]

    def device_id_owner(self, device_id: str) -> str | None:
        """Return the profile id statically claiming device_id, if any."""
        for profile_id, profile in self.profiles.items():
            if device_id in profile.device_ids:
                return profile_id
        return None


DEFAULT_PROFILES_CONFIG = ProfilesConfig(profiles={})


@dataclass
class ReportConfig:
    """Parsed ``[report]`` section controlling PDF/CSV report rendering."""

    unit: str  # "mg_dl" or "mmol_l"
    date_format: str  # "us" or "world"
    layout: str  # "full", "simple", or "chart"
    page_size: str  # "letter" or "a4"
    include_device_id: bool
    include_model: bool
    include_profile: bool
    include_summary: bool
    #: Show Dose/Note columns from a profile's configured sliding_scale.
    #: Requires --profile (or a --multi-meter section resolving to one) --
    #: see dosing.py and report.py's main().
    include_sliding_scale: bool


DEFAULT_REPORT_CONFIG = ReportConfig(
    unit="mg_dl",
    date_format="world",
    layout="full",
    page_size="letter",
    include_device_id=True,
    include_model=True,
    include_profile=False,
    include_summary=False,
    include_sliding_scale=False,
)

_UNITS = ("mg_dl", "mmol_l")
_DATE_FORMATS = ("us", "world")
_LAYOUTS = ("full", "simple", "chart")
_PAGE_SIZES = ("letter", "a4")


@dataclass
class OnboardingConfig:
    """Parsed ``[onboarding]`` section: new-device assignment notifications.

    When a meter with an unrecognized device_id is synced for the first
    time, two things fire together, exactly once per device_id (throttled
    via state_path): an interactive assignment prompt (ntfy with one action
    button per profile if the API is reachable, otherwise a local dunstify
    prompt), and an unconditional, non-interactive Apprise notification to
    admin_apprise_urls -- so an admin is informed even if nobody sees or
    acts on the interactive prompt.
    """

    enabled: bool
    ntfy_url: str
    ntfy_token: str
    api_base_url: str
    dunstify_timeout_seconds: int
    state_path: str
    assignments_path: str
    admin_apprise_urls: list[str]


DEFAULT_ONBOARDING_CONFIG = OnboardingConfig(
    enabled=False,
    ntfy_url="",
    ntfy_token="",
    api_base_url="http://127.0.0.1:8080",
    dunstify_timeout_seconds=30,
    state_path="/var/lib/trividia-truemetrix-daemon/onboarding-state.json",
    assignments_path="/var/lib/trividia-truemetrix-daemon/device-assignments.json",
    admin_apprise_urls=[],
)


@dataclass
class ApiConfig:
    """Parsed ``[api]`` section: local HTTP server for the ntfy assignment callback.

    Deliberately minimal (``/health`` and ``/assign-device`` only) -- there's
    no ``/latest`` or ``/report`` yet, unlike etekcity-scale-daemon's API.
    """

    enabled: bool
    host: str
    port: int
    token: str


DEFAULT_API_CONFIG = ApiConfig(enabled=False, host="127.0.0.1", port=8080, token="")


def _parse_bool(value: str, key: str) -> bool:
    normalized = value.strip().lower()
    if normalized in ("yes", "true", "1", "on"):
        return True
    if normalized in ("no", "false", "0", "off"):
        return False
    raise ConfigError(f"{key} must be yes/no, got {value!r}")


def _read_parser(config_path: str) -> configparser.ConfigParser:
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(
            f"Config file not found: {path}. Copy "
            "config/trividia-truemetrix-daemon.ini.example to this path and edit it."
        )
    parser = configparser.ConfigParser()
    parser.read(path)
    return parser


def load_config(config_path: str) -> DaemonConfig:
    """Load and validate the ``[daemon]``/``[storage]`` sections."""
    parser = _read_parser(config_path)

    daemon = parser["daemon"] if parser.has_section("daemon") else {}
    storage = parser["storage"] if parser.has_section("storage") else {}

    try:
        poll_interval_seconds = float(daemon.get("poll_interval_seconds", "5"))
    except ValueError as exc:
        raise ConfigError("daemon.poll_interval_seconds must be a number") from exc
    if poll_interval_seconds <= 0:
        raise ConfigError("daemon.poll_interval_seconds must be positive")

    db_path = storage.get("db_path", "").strip()
    if not db_path:
        raise ConfigError("storage.db_path must be set")

    return DaemonConfig(
        config_path=Path(config_path),
        poll_interval_seconds=poll_interval_seconds,
        db_path=db_path,
        log_level=daemon.get("log_level", "INFO").strip().upper(),
    )


def load_profiles_config(config_path: str) -> ProfilesConfig:
    """Load every ``[profile.<name>]`` section.

    Raises:
        ConfigError: If a profile has no device_ids, two profiles claim the
            same device_id (ambiguous static attribution), or sliding_scale
            is malformed/ambiguous -- see dosing.parse_sliding_scale.
    """
    parser = _read_parser(config_path)

    profiles: dict[str, ProfileConfig] = {}
    claimed: dict[str, str] = {}  # device_id -> profile name that claimed it

    for section_name in parser.sections():
        if not section_name.startswith("profile."):
            continue
        name = section_name[len("profile."):]
        section = parser[section_name]

        ids_raw = section.get("device_ids", "").strip()
        device_ids = tuple(d.strip() for d in ids_raw.split(",") if d.strip())
        if not device_ids:
            raise ConfigError(f"[{section_name}] device_ids must be set")

        for device_id in device_ids:
            if device_id in claimed:
                raise ConfigError(
                    f"device_id {device_id!r} is claimed by both profile "
                    f"{claimed[device_id]!r} and {name!r} -- each device_id "
                    "may belong to only one profile"
                )
            claimed[device_id] = name

        try:
            sliding_scale = parse_sliding_scale(
                section.get("sliding_scale", ""), context=f"[{section_name}] sliding_scale"
            )
        except SlidingScaleError as exc:
            raise ConfigError(str(exc)) from exc

        profiles[name] = ProfileConfig(
            full_name=section.get("name", "").strip() or name,
            email=section.get("email", "").strip(),
            notes=section.get("notes", "").strip(),
            device_ids=device_ids,
            sliding_scale=sliding_scale,
        )

    return ProfilesConfig(profiles=profiles)


def load_onboarding_config(config_path: str) -> OnboardingConfig:
    """Load the ``[onboarding]`` section, if present."""
    parser = _read_parser(config_path)

    if not parser.has_section("onboarding"):
        return DEFAULT_ONBOARDING_CONFIG

    onboarding = parser["onboarding"]
    enabled = _parse_bool(onboarding.get("enabled", "no"), "onboarding.enabled")

    ntfy_url = onboarding.get("ntfy_url", "").strip()

    try:
        dunstify_timeout_seconds = int(
            onboarding.get(
                "dunstify_timeout_seconds",
                str(DEFAULT_ONBOARDING_CONFIG.dunstify_timeout_seconds),
            )
        )
    except ValueError as exc:
        raise ConfigError("onboarding.dunstify_timeout_seconds must be an integer") from exc

    admin_urls_raw = onboarding.get("admin_apprise_urls", "").strip()
    admin_apprise_urls = [u.strip() for u in admin_urls_raw.split(",") if u.strip()]

    return OnboardingConfig(
        enabled=enabled,
        ntfy_url=ntfy_url,
        ntfy_token=onboarding.get("ntfy_token", "").strip(),
        api_base_url=(
            onboarding.get("api_base_url", DEFAULT_ONBOARDING_CONFIG.api_base_url).strip()
            or DEFAULT_ONBOARDING_CONFIG.api_base_url
        ),
        dunstify_timeout_seconds=dunstify_timeout_seconds,
        state_path=(
            onboarding.get("state_path", DEFAULT_ONBOARDING_CONFIG.state_path).strip()
            or DEFAULT_ONBOARDING_CONFIG.state_path
        ),
        assignments_path=(
            onboarding.get(
                "assignments_path", DEFAULT_ONBOARDING_CONFIG.assignments_path
            ).strip()
            or DEFAULT_ONBOARDING_CONFIG.assignments_path
        ),
        admin_apprise_urls=admin_apprise_urls,
    )


def load_api_config(config_path: str) -> ApiConfig:
    """Load the ``[api]`` section, if present."""
    parser = _read_parser(config_path)

    if not parser.has_section("api"):
        return DEFAULT_API_CONFIG

    api = parser["api"]
    try:
        port = int(api.get("port", str(DEFAULT_API_CONFIG.port)))
    except ValueError as exc:
        raise ConfigError("api.port must be an integer") from exc

    return ApiConfig(
        enabled=_parse_bool(api.get("enabled", "no"), "api.enabled"),
        host=api.get("host", DEFAULT_API_CONFIG.host).strip() or DEFAULT_API_CONFIG.host,
        port=port,
        token=api.get("token", "").strip(),
    )


def load_report_config(config_path: str) -> ReportConfig:
    """Load the ``[report]`` section, if present."""
    parser = _read_parser(config_path)

    if not parser.has_section("report"):
        return DEFAULT_REPORT_CONFIG

    report = parser["report"]

    unit = report.get("unit", DEFAULT_REPORT_CONFIG.unit).strip().lower()
    if unit not in _UNITS:
        raise ConfigError(f"report.unit must be one of {_UNITS}, got {unit!r}")

    date_format = report.get("date_format", DEFAULT_REPORT_CONFIG.date_format).strip().lower()
    if date_format not in _DATE_FORMATS:
        raise ConfigError(
            f"report.date_format must be one of {_DATE_FORMATS}, got {date_format!r}"
        )

    layout = report.get("layout", DEFAULT_REPORT_CONFIG.layout).strip().lower()
    if layout not in _LAYOUTS:
        raise ConfigError(f"report.layout must be one of {_LAYOUTS}, got {layout!r}")

    page_size = report.get("page_size", DEFAULT_REPORT_CONFIG.page_size).strip().lower()
    if page_size not in _PAGE_SIZES:
        raise ConfigError(f"report.page_size must be one of {_PAGE_SIZES}, got {page_size!r}")

    return ReportConfig(
        unit=unit,
        date_format=date_format,
        layout=layout,
        page_size=page_size,
        include_device_id=_parse_bool(
            report.get("include_device_id", "yes"), "report.include_device_id"
        ),
        include_model=_parse_bool(report.get("include_model", "yes"), "report.include_model"),
        include_profile=_parse_bool(
            report.get("include_profile", "no"), "report.include_profile"
        ),
        include_summary=_parse_bool(
            report.get("include_summary", "no"), "report.include_summary"
        ),
        include_sliding_scale=_parse_bool(
            report.get("include_sliding_scale", "no"), "report.include_sliding_scale"
        ),
    )
