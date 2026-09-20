"""Integration tests run against the real PostgreSQL from docker-compose.yml.

They never skip: if the database is not reachable they FAIL with the command that starts it, so a
green run always means the database was really exercised. Exclude them with
``pytest -m "not integration"`` when you only want the fast tests.

Connection details come from the process environment first, then the git-ignored ``.env`` at the
repository root (the same file docker compose reads). Nothing here prints a password.
"""

import os
import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from dotenv import dotenv_values
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from tests.helpers import build_settings

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]  # backend/tests/integration -> repository root
_DOTENV = dotenv_values(_REPO_ROOT / ".env")

# Created by docker/postgres-init/01-roles-and-databases.sh next to the development database.
TEST_DATABASE = "stock_analyst_test"


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or _DOTENV.get(name) or default


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    admin_user: str
    admin_password: str
    app_user: str
    app_password: str
    database: str

    def url(
        self, *, admin: bool = False, port: int | None = None, password: str | None = None
    ) -> URL:
        return URL.create(
            "postgresql+asyncpg",
            username=self.admin_user if admin else self.app_user,
            password=password
            if password is not None
            else (self.admin_password if admin else self.app_password),
            host=self.host,
            port=port or self.port,
            database=self.database,
        )

    def settings(self, **overrides: Any) -> Settings:
        """App settings that connect as the *runtime* role, like the deployed containers do."""
        url = self.url(port=overrides.pop("port", None), password=overrides.pop("credential", None))
        return build_settings(database_url=url.render_as_string(hide_password=False), **overrides)


@pytest.fixture(scope="session")
def db_config() -> DbConfig:
    admin_password = _env("POSTGRES_PASSWORD")
    app_password = _env("APP_DB_PASSWORD")
    if not admin_password or not app_password:
        pytest.fail(
            "POSTGRES_PASSWORD and APP_DB_PASSWORD must be set in .env (copy .env.example), "
            "then start the database with: docker compose up -d --wait"
        )
    config = DbConfig(
        host="127.0.0.1",
        port=int(_env("DB_PORT", "5432") or "5432"),
        admin_user=_env("POSTGRES_USER", "stock_admin") or "stock_admin",
        admin_password=admin_password,
        app_user=_env("APP_DB_USER", "stock_app") or "stock_app",
        app_password=app_password,
        database=TEST_DATABASE,
    )
    try:
        socket.create_connection((config.host, config.port), timeout=2).close()
    except OSError:
        pytest.fail(
            f"PostgreSQL is not reachable at {config.host}:{config.port}. "
            "Start it with: docker compose up -d --wait   (Docker Desktop must be running)"
        )
    return config


@pytest.fixture
async def admin_engine(db_config: DbConfig) -> AsyncIterator[AsyncEngine]:
    """The privileged migration role. NullPool: every use is one short connection."""
    engine = create_async_engine(db_config.url(admin=True), poolclass=NullPool)
    yield engine
    await engine.dispose()


@pytest.fixture
async def app_engine(db_config: DbConfig) -> AsyncIterator[AsyncEngine]:
    """The unprivileged runtime role, exactly what the API and worker will use."""
    engine = create_async_engine(db_config.url(), poolclass=NullPool)
    yield engine
    await engine.dispose()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if _HERE in Path(str(item.path)).parents:
            item.add_marker(pytest.mark.integration)
