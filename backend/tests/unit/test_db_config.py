"""Database settings, engine and session-factory behaviour that needs no running database."""

from collections.abc import AsyncIterator
from typing import cast

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.pool import QueuePool

from app.core.config import Settings
from app.db.engine import create_db_engine, create_session_factory
from tests.helpers import build_settings

SECRET = "hunter2-s3cret"  # noqa: S105  (a fake password, used to prove it never leaks)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    created = create_db_engine(build_settings(db_pool_size=3, db_max_overflow=2))
    yield created
    await created.dispose()


# --- settings ------------------------------------------------------------------------------


def test_database_url_is_required() -> None:
    """A deployment with no database configured must crash at startup, not at first request."""
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    "url",
    [
        f"postgresql://user:{SECRET}@localhost/db",  # sync driver: would block the event loop
        f"postgresql+psycopg://user:{SECRET}@localhost/db",
        f"sqlite+aiosqlite:///{SECRET}.db",
        "not a url",
    ],
)
def test_only_the_asyncpg_driver_is_accepted(url: str) -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, database_url=url)
    # The startup crash message is written to logs, so it must not echo the password back.
    assert SECRET not in str(caught.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("db_pool_size", 0),
        ("db_pool_size", 500),
        ("db_max_overflow", -1),
        ("db_pool_timeout_seconds", 0),
        ("db_connect_timeout_seconds", -1),
        ("db_ready_timeout_seconds", 0),
    ],
)
def test_out_of_range_pool_settings_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        build_settings(**{field: value})


def test_pool_defaults_are_small_and_sane() -> None:
    settings = build_settings()
    assert (settings.db_pool_size, settings.db_max_overflow) == (5, 5)
    assert settings.db_ready_timeout_seconds < settings.db_pool_timeout_seconds


def test_the_password_never_appears_in_repr_or_str() -> None:
    settings = build_settings(database_url=f"postgresql+asyncpg://user:{SECRET}@localhost/db")
    assert SECRET not in repr(settings)
    assert SECRET not in str(settings)
    assert settings.database_url.get_secret_value().endswith("localhost/db")


# --- engine --------------------------------------------------------------------------------


def test_engine_uses_the_async_driver_and_configured_pool(engine: AsyncEngine) -> None:
    pool = cast(QueuePool, engine.pool)
    assert engine.dialect.driver == "asyncpg"
    assert pool.size() == 3
    assert pool._max_overflow == 2
    assert pool.timeout() == 5.0
    # pre_ping: a connection killed by a DB restart or idle timeout is replaced, not handed out.
    assert pool._pre_ping is True


def test_creating_an_engine_opens_no_connection(engine: AsyncEngine) -> None:
    """The URL points at nothing real; creation must still succeed. Connecting is lazy."""
    assert cast(QueuePool, engine.pool).checkedout() == 0


# --- sessions ------------------------------------------------------------------------------


async def test_session_factory_builds_short_lived_async_sessions(engine: AsyncEngine) -> None:
    factory = create_session_factory(engine)
    session = factory()
    try:
        assert isinstance(session, AsyncSession)
        # Lazy loading cannot work under asyncio, so attributes must survive a commit.
        assert session.sync_session.expire_on_commit is False
        assert session.bind is engine
    finally:
        await session.close()
    assert cast(QueuePool, engine.pool).checkedout() == 0
