"""Integration tests run against the real PostgreSQL from docker-compose.yml.

They never skip: if the database is not reachable they FAIL with the command that starts it, so a
green run always means the database was really exercised. Exclude them with
``pytest -m "not integration"`` when you only want the fast tests.

Connection details come from the process environment first, then the git-ignored ``.env`` at the
repository root (the same file docker compose reads). Nothing here prints a password.
"""

import asyncio
import os
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import URL, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings
from tests.helpers import build_settings

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parents[1]  # backend/tests/integration -> backend
_REPO_ROOT = _HERE.parents[2]  # ... -> repository root
_DOTENV = dotenv_values(_REPO_ROOT / ".env")

ALEMBIC_INI = _BACKEND / "alembic.ini"
MIGRATIONS_DIR = _BACKEND / "migrations"

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


class Migrator:
    """Runs real Alembic commands against the TEST database as the migration role.

    Alembic's async env.py calls ``asyncio.run``, which cannot be called from inside the running
    event loop of a pytest-asyncio test, so each command runs in a worker thread
    (``asyncio.to_thread``).
    """

    def __init__(self, config: Config) -> None:
        self.config = config

    async def upgrade(self, revision: str = "head") -> None:
        await asyncio.to_thread(command.upgrade, self.config, revision)

    async def downgrade(self, revision: str = "base") -> None:
        await asyncio.to_thread(command.downgrade, self.config, revision)


def make_alembic_config(*, env_file: Path | None = None) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    # Tests must never pick up a developer's real .env.migration.
    config.set_main_option("migration_env_file", str(env_file or _REPO_ROOT / "no-such-file"))
    # Do not let Alembic reconfigure the logging of the pytest process it runs inside.
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture(scope="session", autouse=True)
def rebuilt_test_database(db_config: DbConfig) -> None:
    """Start every integration session from a database built by the migrations alone.

    Without this the tests would trust whatever an earlier run (or an edited migration) left behind.
    Runs synchronously, so Alembic's own ``asyncio.run`` has no running event loop to clash with.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(
            "MIGRATION_DATABASE_URL",
            db_config.url(admin=True).render_as_string(hide_password=False),
        )
        config = make_alembic_config()
        command.downgrade(config, "base")
        command.upgrade(config, "head")


@pytest.fixture
def migrator(db_config: DbConfig, monkeypatch: pytest.MonkeyPatch) -> Migrator:
    """The migration URL comes from the environment, exactly as in the one-off AWS task."""
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL", db_config.url(admin=True).render_as_string(hide_password=False)
    )
    return Migrator(make_alembic_config())


@pytest.fixture
async def migrated_db(migrator: Migrator) -> None:
    """The test database at the latest revision. It is left at head afterwards."""
    await migrator.upgrade("head")


@pytest.fixture
def session_factory(app_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Sessions for the runtime role, the way application code gets them."""
    return async_sessionmaker(app_engine, expire_on_commit=False)


@pytest.fixture
async def clean_auth_tables(admin_engine: AsyncEngine) -> None:
    """Every auth test starts with no users and no sessions (the rows, not the tables)."""
    async with admin_engine.begin() as connection:
        await connection.execute(text("TRUNCATE sessions, users CASCADE"))


MakeUser = Callable[..., Awaitable[UUID]]


@pytest.fixture
def make_user(admin_engine: AsyncEngine, clean_auth_tables: None) -> MakeUser:
    """Insert a user directly (P4a has no login yet) and return its id."""

    async def make(*, sub: str | None = None, email: str = "reader@example.test") -> UUID:
        async with admin_engine.begin() as connection:
            result = await connection.execute(
                text("INSERT INTO users (google_sub, email) VALUES (:sub, :email) RETURNING id"),
                {"sub": sub or f"sub-{uuid4()}", "email": email},
            )
            user_id: UUID = result.scalar_one()
            return user_id

    return make


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if _HERE in Path(str(item.path)).parents:
            item.add_marker(pytest.mark.integration)
