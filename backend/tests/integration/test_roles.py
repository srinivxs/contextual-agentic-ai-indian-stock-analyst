"""The migration-role / runtime-role boundary, proven against the real database.

The SQL below is written out literally on purpose (no string-built statements, even in tests).
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import DbConfig

ROLE_FLAGS = (
    "SELECT current_user AS name, rolsuper, rolcreatedb, rolcreaterole, rolreplication "
    "FROM pg_roles WHERE rolname = current_user"
)
CONNECTABLE_DATABASES = (
    "SELECT datname FROM pg_database "
    "WHERE has_database_privilege(:role, datname, 'CONNECT') "
    "AND NOT datistemplate AND datname <> 'postgres'"
)


async def test_the_runtime_role_connects_and_is_unprivileged(
    app_engine: AsyncEngine, db_config: DbConfig
) -> None:
    async with app_engine.connect() as connection:
        row = (await connection.execute(text(ROLE_FLAGS))).one()
    assert row.name == db_config.app_user
    assert (row.rolsuper, row.rolcreatedb, row.rolcreaterole, row.rolreplication) == (
        False,
        False,
        False,
        False,
    )


async def test_the_runtime_role_cannot_create_tables(app_engine: AsyncEngine) -> None:
    """No DDL at runtime: a bug or an injection cannot add or reshape tables."""
    async with app_engine.connect() as connection:
        with pytest.raises(DBAPIError, match="permission denied"):
            await connection.execute(text("CREATE TABLE p3a_must_not_exist (id int)"))


async def test_tables_made_by_the_migration_role_are_usable_but_not_alterable_at_runtime(
    admin_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    """Default privileges: what a migration creates, the app can read and write, nothing more."""
    async with admin_engine.begin() as admin:
        await admin.execute(text("DROP TABLE IF EXISTS p3a_privilege_probe"))
        await admin.execute(
            text(
                "CREATE TABLE p3a_privilege_probe "
                "(id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, note text)"
            )
        )
    try:
        async with app_engine.begin() as app:
            await app.execute(text("INSERT INTO p3a_privilege_probe (note) VALUES ('hello')"))
            note = (await app.execute(text("SELECT note FROM p3a_privilege_probe"))).scalar_one()
            assert note == "hello"
            await app.execute(text("UPDATE p3a_privilege_probe SET note = 'changed'"))
            await app.execute(text("DELETE FROM p3a_privilege_probe"))

        for statement in (
            "DROP TABLE p3a_privilege_probe",
            "ALTER TABLE p3a_privilege_probe ADD COLUMN extra int",
            "TRUNCATE p3a_privilege_probe",
        ):
            async with app_engine.connect() as app:
                with pytest.raises(DBAPIError):
                    await app.execute(text(statement))
    finally:
        async with admin_engine.begin() as admin:
            await admin.execute(text("DROP TABLE IF EXISTS p3a_privilege_probe"))


async def test_the_runtime_role_is_scoped_to_the_application_databases(
    db_config: DbConfig, admin_engine: AsyncEngine
) -> None:
    async with admin_engine.connect() as admin:
        result = await admin.execute(text(CONNECTABLE_DATABASES), {"role": db_config.app_user})
        databases = set(result.scalars().all())
    assert databases <= {"stock_analyst", db_config.database}
    assert db_config.database in databases
