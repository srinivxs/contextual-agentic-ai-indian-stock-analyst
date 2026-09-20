"""Pooling, short-lived sessions, and recovery from a killed connection, on the real database."""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.helpers import running_app
from tests.integration.conftest import DbConfig

APP_CONNECTIONS = (
    "SELECT count(*) FROM pg_stat_activity WHERE datname = :db AND usename = :role "
    "AND pid <> pg_backend_pid()"
)


async def test_concurrent_work_never_exceeds_the_pool_limit_and_releases_connections(
    db_config: DbConfig, admin_engine: AsyncEngine
) -> None:
    """Ten sessions each hold a connection for 0.2 s, but the pool allows only 2 + 1 = 3."""
    settings = db_config.settings(db_pool_size=2, db_max_overflow=1, db_pool_timeout_seconds=30)
    peak = 0

    async def watch_connections() -> None:
        nonlocal peak
        async with admin_engine.connect() as admin:
            while True:
                count = (
                    await admin.execute(
                        text(APP_CONNECTIONS),
                        {"db": db_config.database, "role": db_config.app_user},
                    )
                ).scalar_one()
                peak = max(peak, count)
                await asyncio.sleep(0.02)

    async def slow_query(factory: async_sessionmaker[AsyncSession]) -> None:
        async with factory() as session:
            await session.execute(text("SELECT pg_sleep(0.2)"))

    async with running_app(settings) as (app, _):
        watcher = asyncio.create_task(watch_connections())
        try:
            async with asyncio.TaskGroup() as group:
                for _ in range(10):
                    group.create_task(slow_query(app.state.session_factory))
        finally:
            watcher.cancel()

        assert 1 <= peak <= 3
        assert app.state.engine.pool.checkedout() == 0  # every session gave its connection back


async def test_a_burst_of_readiness_checks_leaks_no_connections(db_config: DbConfig) -> None:
    settings = db_config.settings(db_pool_size=2, db_max_overflow=1, db_pool_timeout_seconds=30)
    async with running_app(settings) as (app, client):
        responses = await asyncio.gather(*(client.get("/api/readyz") for _ in range(50)))
        assert {r.status_code for r in responses} == {200}
        assert app.state.engine.pool.checkedout() == 0


async def test_a_connection_killed_by_the_server_is_replaced_not_handed_out(
    db_config: DbConfig, admin_engine: AsyncEngine
) -> None:
    """pool_pre_ping: after a DB restart or idle cut-off the next request still succeeds."""
    async with running_app(db_config.settings(db_pool_size=1, db_max_overflow=0)) as (_, client):
        assert (await client.get("/api/readyz")).status_code == 200  # connection now pooled

        async with admin_engine.connect() as admin:
            await admin.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :db AND usename = :role AND pid <> pg_backend_pid()"
                ),
                {"db": db_config.database, "role": db_config.app_user},
            )

        assert (await client.get("/api/readyz")).status_code == 200
