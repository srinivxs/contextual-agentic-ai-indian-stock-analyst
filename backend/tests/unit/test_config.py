from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from tests.helpers import TEST_DATABASE_URL


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_defaults() -> None:
    settings = Settings(_env_file=None, database_url=TEST_DATABASE_URL)
    assert settings.app_env == "local"
    assert settings.log_level == "INFO"


def test_reads_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("LOG_LEVEL", "debug")  # lower-case is accepted and normalised
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    settings = Settings(_env_file=None)
    assert settings.app_env == "production"
    assert settings.log_level == "DEBUG"


def test_rejects_invalid_log_level() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=TEST_DATABASE_URL, log_level="LOUD")


def test_rejects_invalid_app_env() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url=TEST_DATABASE_URL, app_env="staging")


def test_env_file_is_read_and_unknown_keys_are_ignored(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    # Variables for later milestones (for example GOOGLE_CLIENT_ID) must not break startup today.
    env_file.write_text(
        f"DATABASE_URL={TEST_DATABASE_URL}\nGOOGLE_CLIENT_ID=ignored\nLOG_LEVEL=WARNING\n"
    )
    settings = Settings(_env_file=env_file)
    assert settings.log_level == "WARNING"


def test_real_environment_overrides_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"DATABASE_URL={TEST_DATABASE_URL}\nLOG_LEVEL=WARNING\n")
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    assert Settings(_env_file=env_file).log_level == "ERROR"


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    get_settings.cache_clear()
    assert get_settings() is get_settings()
    get_settings.cache_clear()
