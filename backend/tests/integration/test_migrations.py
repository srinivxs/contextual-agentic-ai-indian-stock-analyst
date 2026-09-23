"""Alembic against the real test database: upgrade, downgrade, upgrade, verified at every step."""

from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import DbConfig, Migrator, make_alembic_config

HEAD = "0005"

STATE_QUERIES = {
    "extension": "SELECT extversion FROM pg_extension WHERE extname = 'vector'",
    "stocks_table": "SELECT to_regclass('public.stocks') IS NOT NULL",
    "users_table": "SELECT to_regclass('public.users') IS NOT NULL",
    "sessions_table": "SELECT to_regclass('public.sessions') IS NOT NULL",
    "follows_table": "SELECT to_regclass('public.user_follows') IS NOT NULL",
    "ingestion_tables": (
        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
        "AND tablename IN ('documents', 'document_pages', 'chunks', 'jobs')"
    ),
    "version_table": "SELECT to_regclass('public.alembic_version') IS NOT NULL",
}


@dataclass(frozen=True)
class State:
    """What the database looks like right now, as the migration role sees it."""

    vector_extension: bool
    stocks_table: bool
    users_table: bool
    sessions_table: bool
    follows_table: bool
    ingestion_tables: int  # documents, document_pages, chunks, jobs (0004)
    revision: str | None
    stock_rows: tuple[tuple[object, ...], ...]


async def snapshot(admin_engine: AsyncEngine) -> State:
    async with admin_engine.connect() as connection:

        async def scalar(name: str) -> object:
            return (await connection.execute(text(STATE_QUERIES[name]))).scalar_one_or_none()

        extension = await scalar("extension")
        has_stocks = await scalar("stocks_table")
        has_version = await scalar("version_table")
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
        return State(
            vector_extension=extension is not None,
            stocks_table=bool(has_stocks),
            users_table=bool(await scalar("users_table")),
            sessions_table=bool(await scalar("sessions_table")),
            follows_table=bool(await scalar("follows_table")),
            ingestion_tables=int(str(await scalar("ingestion_tables"))),
            revision=revision,
            stock_rows=rows,
        )


EMPTY = State(
    vector_extension=False,
    stocks_table=False,
    users_table=False,
    sessions_table=False,
    follows_table=False,
    ingestion_tables=0,
    revision=None,
    stock_rows=(),
)


async def test_up_down_up_round_trip_verified_at_every_step(
    migrator: Migrator, admin_engine: AsyncEngine
) -> None:
    await migrator.downgrade("base")
    assert await snapshot(admin_engine) == EMPTY  # step 0: a database with nothing in it

    await migrator.upgrade("head")
    first = await snapshot(admin_engine)
    assert first.vector_extension is True
    assert first.stocks_table is True
    assert first.users_table is True
    assert first.sessions_table is True
    assert first.follows_table is True
    assert first.ingestion_tables == 4
    assert first.revision == HEAD
    assert [row[1] for row in first.stock_rows] == ["RELIANCE", "TCS", "HDFCBANK"]

    await migrator.downgrade("base")
    assert await snapshot(admin_engine) == EMPTY  # everything the migrations made is gone again

    await migrator.upgrade("head")
    assert await snapshot(admin_engine) == first  # and coming back gives exactly the same result


async def test_each_downgrade_step_removes_only_what_its_revision_created(
    migrator: Migrator, admin_engine: AsyncEngine
) -> None:
    """0004 owns the ingestion tables, 0003 user_follows, 0002 users and sessions, 0001 stocks."""
    await migrator.upgrade("head")
    at_head = await snapshot(admin_engine)

    await migrator.downgrade("-1")  # 0005 only adds two columns to documents
    assert (await snapshot(admin_engine)).revision == "0004"

    await migrator.downgrade("-1")
    no_ingestion = await snapshot(admin_engine)
    assert no_ingestion.revision == "0003"
    assert no_ingestion.ingestion_tables == 0
    assert no_ingestion.follows_table is True  # everything older is untouched

    await migrator.downgrade("-1")
    no_follows = await snapshot(admin_engine)
    assert no_follows.revision == "0002"
    assert no_follows.follows_table is False
    assert no_follows.users_table is True  # the tables follows depended on are still there
    assert no_follows.sessions_table is True

    await migrator.downgrade("-1")
    no_auth = await snapshot(admin_engine)
    assert no_auth.revision == "0001"
    assert no_auth.users_table is False
    assert no_auth.sessions_table is False
    assert no_auth.vector_extension is True  # still installed
    assert no_auth.stocks_table is True
    assert no_auth.stock_rows == at_head.stock_rows  # the seed rows are untouched

    await migrator.downgrade("-1")
    assert await snapshot(admin_engine) == EMPTY

    await migrator.upgrade("head")  # leave the test database at head for the other tests
    assert await snapshot(admin_engine) == at_head


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
    assert (await snapshot(admin_engine)).revision == HEAD


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
    assert (await snapshot(admin_engine)).revision == HEAD


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
