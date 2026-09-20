"""Typed application configuration.

Settings come from real environment variables first, then an optional ``.env`` file at the repo
root. Values are validated when ``Settings`` is constructed, so a bad deployment crashes at
startup with a clear error instead of misbehaving later. Only variables the code currently uses
are declared; each milestone adds its own (see ``.env.example`` for the full planned list).
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
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
        # A rejected DATABASE_URL would otherwise be echoed, password included, into the startup
        # error and from there into logs.
        hide_input_in_errors=True,
    )

    app_env: Literal["local", "test", "production"] = "local"
    log_level: LogLevel = "INFO"

    # The RUNTIME role's URL (no DDL rights). Migrations use a separate, privileged URL (P3b).
    database_url: SecretStr
    # Each process may hold at most pool_size + max_overflow connections. The API and the worker
    # add up, and the total must stay well under the server's max_connections.
    db_pool_size: int = Field(default=5, ge=1, le=20)
    db_max_overflow: int = Field(default=5, ge=0, le=20)
    db_pool_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    db_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    # How long /api/readyz waits for `SELECT 1`. Kept below the pool timeout on purpose.
    db_ready_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    @field_validator("database_url")
    @classmethod
    def _require_the_asyncpg_driver(cls, value: SecretStr) -> SecretStr:
        # Any other driver is synchronous and would block the event loop on every query.
        if not value.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("database_url must start with postgresql+asyncpg://")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: object) -> object:
        return value.upper() if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    """Process-wide settings, built once. Tests construct ``Settings`` directly instead."""
    return Settings()
