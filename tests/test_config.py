from __future__ import annotations

import pytest

from trividia_truemetrix_daemon.config import (
    ConfigError,
    load_api_config,
    load_config,
    load_onboarding_config,
    load_profiles_config,
)


def _write(tmp_path, content: str):
    path = tmp_path / "config.ini"
    path.write_text(content)
    return str(path)


def test_load_config_requires_db_path(tmp_path):
    path = _write(tmp_path, "[daemon]\nlog_level = DEBUG\n")
    with pytest.raises(ConfigError, match="storage.db_path"):
        load_config(path)


def test_load_config_defaults(tmp_path):
    path = _write(tmp_path, "[storage]\ndb_path = /tmp/readings.db\n")
    config = load_config(path)
    assert config.db_path == "/tmp/readings.db"
    assert config.poll_interval_seconds == 5
    assert config.log_level == "INFO"


def test_load_config_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        load_config("/nonexistent/path.ini")


def test_load_config_rejects_non_positive_poll_interval(tmp_path):
    path = _write(
        tmp_path,
        "[storage]\ndb_path = /tmp/x.db\n[daemon]\npoll_interval_seconds = 0\n",
    )
    with pytest.raises(ConfigError, match="positive"):
        load_config(path)


def test_load_profiles_config_parses_device_ids(tmp_path):
    path = _write(
        tmp_path,
        "[profile.Alice]\n"
        "email = alice@example.com\n"
        "device_ids = Trividia-BLU-11111111, Trividia-BLU-22222222\n"
        "[profile.Bob]\n"
        "device_ids = Trividia-BLU-33333333\n",
    )
    config = load_profiles_config(path)
    assert set(config.profiles) == {"Alice", "Bob"}
    assert config.profiles["Alice"].email == "alice@example.com"
    assert config.profiles["Alice"].device_ids == (
        "Trividia-BLU-11111111",
        "Trividia-BLU-22222222",
    )
    assert config.device_id_owner("Trividia-BLU-33333333") == "Bob"
    assert config.device_id_owner("Trividia-BLU-99999999") is None
    # No [profile.Bob] name= given -- full_name defaults to the profile id.
    assert config.profiles["Bob"].full_name == "Bob"


def test_load_profiles_config_full_name_defaults_to_id(tmp_path):
    path = _write(tmp_path, "[profile.Alice]\ndevice_ids = Trividia-BLU-11111111\n")
    config = load_profiles_config(path)
    assert config.profiles["Alice"].full_name == "Alice"


def test_load_profiles_config_full_name_overridable(tmp_path):
    path = _write(
        tmp_path,
        "[profile.Alice]\nname = Alice Smith\ndevice_ids = Trividia-BLU-11111111\n",
    )
    config = load_profiles_config(path)
    assert config.profiles["Alice"].full_name == "Alice Smith"
    # The id used for matching/action-button-labels is still the section
    # suffix, not the display name.
    assert config.device_id_owner("Trividia-BLU-11111111") == "Alice"


def test_load_profiles_config_parses_notes_and_sliding_scale(tmp_path):
    path = _write(
        tmp_path,
        "[profile.Alice]\n"
        "device_ids = Trividia-BLU-11111111\n"
        "notes = Type 1, uses Humalog\n"
        "sliding_scale =\n"
        "    :70:0:hypo, do not dose\n"
        "    71:150:0\n"
        "    151:200:2\n",
    )
    config = load_profiles_config(path)
    alice = config.profiles["Alice"]
    assert alice.notes == "Type 1, uses Humalog"
    assert len(alice.sliding_scale) == 3
    assert alice.sliding_scale[0].label == "hypo, do not dose"


def test_load_profiles_config_defaults_notes_and_sliding_scale_empty(tmp_path):
    path = _write(tmp_path, "[profile.Alice]\ndevice_ids = Trividia-BLU-11111111\n")
    config = load_profiles_config(path)
    alice = config.profiles["Alice"]
    assert alice.notes == ""
    assert alice.sliding_scale == ()


def test_load_profiles_config_propagates_sliding_scale_errors(tmp_path):
    path = _write(
        tmp_path,
        "[profile.Alice]\n"
        "device_ids = Trividia-BLU-11111111\n"
        "sliding_scale =\n"
        "    71:150:-2\n",
    )
    with pytest.raises(ConfigError, match="negative dose"):
        load_profiles_config(path)


def test_load_profiles_config_requires_device_ids(tmp_path):
    path = _write(tmp_path, "[profile.Alice]\nemail = alice@example.com\n")
    with pytest.raises(ConfigError, match="device_ids"):
        load_profiles_config(path)


def test_load_profiles_config_rejects_duplicate_device_id_claims(tmp_path):
    path = _write(
        tmp_path,
        "[profile.Alice]\ndevice_ids = Trividia-BLU-11111111\n"
        "[profile.Bob]\ndevice_ids = Trividia-BLU-11111111\n",
    )
    with pytest.raises(ConfigError, match="claimed by both"):
        load_profiles_config(path)


def test_load_onboarding_config_defaults_to_disabled(tmp_path):
    path = _write(tmp_path, "[storage]\ndb_path = /tmp/x.db\n")
    config = load_onboarding_config(path)
    assert config.enabled is False
    assert config.admin_apprise_urls == []


def test_load_onboarding_config_parses_admin_urls(tmp_path):
    path = _write(
        tmp_path,
        "[onboarding]\n"
        "enabled = yes\n"
        "ntfy_url = https://ntfy.sh/my-topic\n"
        "admin_apprise_urls = mailto://user:pass@gmail.com, tgram://token/chat\n",
    )
    config = load_onboarding_config(path)
    assert config.enabled is True
    assert config.ntfy_url == "https://ntfy.sh/my-topic"
    assert config.admin_apprise_urls == [
        "mailto://user:pass@gmail.com",
        "tgram://token/chat",
    ]


def test_load_api_config_defaults_to_disabled_loopback(tmp_path):
    path = _write(tmp_path, "[storage]\ndb_path = /tmp/x.db\n")
    config = load_api_config(path)
    assert config.enabled is False
    assert config.host == "127.0.0.1"
    assert config.port == 8080


def test_load_api_config_rejects_bad_port(tmp_path):
    path = _write(tmp_path, "[api]\nenabled = yes\nport = not-a-number\n")
    with pytest.raises(ConfigError, match="port"):
        load_api_config(path)
