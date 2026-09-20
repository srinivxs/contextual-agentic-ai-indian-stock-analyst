"""Alembic against the real test database: upgrade, downgrade, upgrade, verified at every step."""

from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import DbConfig, Migrator, make_alembic_config

STATE_QUERIES = {
    "extension": "SELECT extversion FROM pg_extension WHERE extname = 'vector'",
    "stocks_table": "SELECT to_regclass('public.stocks') IS NOT NULL",
    "version_table": "SELECT to_regclass('public.alembic_version') IS NOT NULL",
}


@dataclass(frozen=True)
class State:
    """What the database looks like right now, as the migration role sees it."""

    vector_extension: bool
    stocks_table: bool
    revision: str | None
    stock_rows: tuple[tuple[object, ...], ...]


async def snapshot(admin_engine: AsyncEngine) -> State:
    async with admin_engine.connect() as connection:
        extension = (
            await connection.execute(text(STATE_QUERIES["extension"]))
        ).scalar_one_or_none()
        has_stocks = (await connection.execute(text(STATE_QUERIES["stocks_table"]))).scalar_one()
        has_version = (await connection.execute(text(STATE_QUERIES["version_table"]))).scalar_one()
        revision = None
        if has_version:
            revision = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
        rows: tuple[tuple[object, ...], ...] = ()
        if has_stocks:
            result = await connection.execute(
                text(
                    "SELECT id, symbol, name, bse_code, sector, is_financial "
                    "FROM stocks ORDER BY id"
                )
            )
            rows = tuple(tuple(row) for row in result)
    return State(extension is not None, has_stocks, revision, rows)


EMPTY = State(vector_extension=False, stocks_table=False, revision=None, stock_rows=())


async def test_up_down_up_round_trip_verified_at_every_step(
    migrator: Migrator, admin_engine: AsyncEngine
) -> None:
    await migrator.downgrade("base")
    assert await snapshot(admin_engine) == EMPTY  # step 0: a database with nothing in it

    await migrator.upgrade("head")
    first = await snapshot(admin_engine)
    assert first.vector_extension is True
    assert first.stocks_table is True
    assert first.revision == "0001"
    assert [row[1] for row in first.stock_rows] == ["RELIANCE", "TCS", "HDFCBANK"]

    await migrator.downgrade("base")
    assert await snapshot(admin_engine) == EMPTY  # everything the migration made is gone again

    await migrator.upgrade("head")
    assert await snapshot(admin_engine) == first  # and coming back gives exactly the same result


async def test_downgrade_one_step_from_head_returns_to_an_empty_database(
    migrator: Migrator, admin_engine: AsyncEngine
) -> None:
    await migrator.upgrade("head")
    await migrator.downgrade("-1")
    assert await snapshot(admin_engine) == EMPTY
    await migrator.upgrade("head")  # leave the test database at head for the other tests


async def test_running_upgrade_twice_changes_nothing(
    migrator: Migrator, admin_engine: AsyncEngine
) -> None:
    await migrator.upgrade("head")
    before = await snapshot(admin_engine)
    await migrator.upgrade("head")
    assert await snapshot(admin_engine) == before
    assert len(before.stock_rows) == 3  # no duplicate seed rows


async def test_the_migration_refuses_to_run_without_a_migration_url(
    db_config: DbConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    missing = Migrator(make_alembic_config())  # its env-file option points at a file that is absent
    with pytest.raises(RuntimeError, match="MIGRATION_DATABASE_URL"):
        await missing.upgrade("head")


async def test_the_url_may_come_from_a_separate_migration_env_file(
    db_config: DbConfig,
    admin_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Local convenience: `.env.migration` holds the admin URL, kept apart from the API's `.env`."""
    env_file = tmp_path / ".env.migration"
    url = db_config.url(admin=True).render_as_string(hide_password=False)
    env_file.write_text(f"MIGRATION_DATABASE_URL={url}\n", encoding="utf-8")
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    from_file = Migrator(make_alembic_config(env_file=env_file))

    await from_file.downgrade("base")
    assert await snapshot(admin_engine) == EMPTY
    await from_file.upgrade("head")
    assert (await snapshot(admin_engine)).revision == "0001"


async def test_the_environment_wins_over_the_env_file(
    db_config: DbConfig,
    admin_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_file = tmp_path / ".env.migration"
    env_file.write_text(
        "MIGRATION_DATABASE_URL=postgresql+asyncpg://nobody:wrong@127.0.0.1:1/nothing\n",
        encoding="utf-8",
    )
    good = db_config.url(admin=True).render_as_string(hide_password=False)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", good)

    # Would fail (connection refused on port 1) if the file's URL were used instead of the variable.
    await Migrator(make_alembic_config(env_file=env_file)).upgrade("head")
    assert (await snapshot(admin_engine)).revision == "0001"


async def test_the_runtime_role_cannot_run_migrations(
    migrator: Migrator,
    db_config: DbConfig,
    admin_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handing Alembic the runtime URL must fail cleanly: that role has no DDL rights."""
    admin_url = db_config.url(admin=True).render_as_string(hide_password=False)
    runtime_url = db_config.url().render_as_string(hide_password=False)
    await migrator.downgrade("base")  # the fixture's environment holds the admin URL

    monkeypatch.setenv("MIGRATION_DATABASE_URL", runtime_url)
    with pytest.raises(DBAPIError, match="permission denied"):
        await migrator.upgrade("head")
    assert await snapshot(admin_engine) == EMPTY  # nothing was half-applied

    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    await migrator.upgrade("head")  # leave the test database at head for the other tests
