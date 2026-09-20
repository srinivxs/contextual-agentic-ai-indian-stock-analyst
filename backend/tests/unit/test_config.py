from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from tests.helpers import (
    TEST_DATABASE_URL,
    TEST_GOOGLE_CLIENT_ID,
    TEST_GOOGLE_CLIENT_SECRET,
    TEST_PUBLIC_BASE_URL,
    TEST_SESSION_SECRET,
)

# Everything the application refuses to start without. Each milestone that adds a required setting
# adds it here too; `test_google_settings.py` proves each one is genuinely required.
REQUIRED: dict[str, Any] = {
    "database_url": TEST_DATABASE_URL,
    "public_base_url": TEST_PUBLIC_BASE_URL,
    "google_client_id": TEST_GOOGLE_CLIENT_ID,
    "google_client_secret": TEST_GOOGLE_CLIENT_SECRET,
    "session_secret": TEST_SESSION_SECRET,
}
REQUIRED_LINES = "".join(f"{name.upper()}={value}\n" for name, value in REQUIRED.items())


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's real environment must never decide the outcome of these tests."""
    for name in ("APP_ENV", "LOG_LEVEL", "COOKIE_SECURE", *(key.upper() for key in REQUIRED)):
        monkeypatch.delenv(name, raising=False)


def test_defaults() -> None:
    settings = Settings(_env_file=None, **REQUIRED)
    assert settings.app_env == "local"
    assert settings.log_level == "INFO"


def test_reads_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name.upper(), value)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOG_LEVEL", "debug")  # lower-case is accepted and normalised
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://app.example.test")  # production rules
    monkeypatch.setenv("COOKIE_SECURE", "true")
    settings = Settings(_env_file=None)
    assert settings.app_env == "production"
    assert settings.log_level == "DEBUG"


def test_rejects_invalid_log_level() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, log_level="LOUD", **REQUIRED)


def test_rejects_invalid_app_env() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="staging", **REQUIRED)


def test_env_file_is_read_and_unknown_keys_are_ignored(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    # Variables for later milestones must not break startup today.
    env_file.write_text(f"{REQUIRED_LINES}A_FUTURE_SETTING=ignored\nLOG_LEVEL=WARNING\n")
    settings = Settings(_env_file=env_file)
    assert settings.log_level == "WARNING"


def test_real_environment_overrides_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"{REQUIRED_LINES}LOG_LEVEL=WARNING\n")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    assert Settings(_env_file=env_file).log_level == "ERROR"


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name.upper(), value)
    get_settings.cache_clear()
    assert get_settings() is get_settings()
    get_settings.cache_clear()
