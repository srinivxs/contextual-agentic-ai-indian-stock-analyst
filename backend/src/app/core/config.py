"""Typed application configuration.

Settings come from real environment variables first, then an optional ``.env`` file at the repo
root. Values are validated when ``Settings`` is constructed, so a bad deployment crashes at
startup with a clear error instead of misbehaving later. Only variables the code currently uses
are declared; each milestone adds its own (see ``.env.example`` for the full planned list).
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/src/app/core/config.py -> parents[4] is the repository root.
_REPO_ROOT = Path(__file__).resolve().parents[4]

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        # .env.example lists variables for later milestones; unknown keys must not break startup.
        extra="ignore",
    )

    app_env: Literal["local", "test", "production"] = "local"
    log_level: LogLevel = "INFO"

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings, built once. Tests construct ``Settings`` directly instead."""
    return Settings()
