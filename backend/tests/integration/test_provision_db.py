"""The runtime-role bootstrap, proven against the real database.

``tests/integration/test_roles.py`` proves the boundary that the *local init script* produces. This
file proves that ``app.db.provision`` produces the same boundary on a database that never ran that
script — which is exactly the situation on RDS (ADR 011).

It works on a throwaway role, ``p7_provision_probe``, so the real ``stock_app`` that the rest of the
suite connects as is never touched.

The SQL below is written out literally, like its neighbour: the only string building in this area of
the codebase is inside ``app.db.provision``, where PostgreSQL leaves no alternative.
"""

from collections.abc import AsyncIterator

import pytest

# asyncpg ships no type stubs, and this is the only place we name one of its exceptions
# directly. A targeted ignore keeps mypy strict everywhere else.
from asyncpg.exceptions import InvalidPasswordError  # type: ignore[import-untyped]
from sqlalchemy import URL, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.provision import provision
from tests.integration.conftest import DbConfig

PROBE_ROLE = "p7_provision_probe"

# Alphanumeric, 32 characters: the shape `random_password` with `special = false` produces, and the
# only shape app.db.provision accepts.
FIRST_CREDENTIAL = "Pw1aaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SECOND_CREDENTIAL = "Pw2bbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

ROLE_EXISTS = "SELECT 1 FROM pg_roles WHERE rolname = 'p7_provision_probe'"
ROLE_FLAGS = (
    "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication "
    "FROM pg_roles WHERE rolname = 'p7_provision_probe'"
)
CLEANUP = (
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM p7_provision_probe",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM p7_provision_probe",
    "DROP OWNED BY p7_provision_probe",
    "DROP ROLE p7_provision_probe",
)


async def _drop_probe_role(admin_engine: AsyncEngine) -> None:
    async with admin_engine.begin() as admin:
        if (await admin.execute(text(ROLE_EXISTS))).first() is None:
            return
        for statement in CLEANUP:
            await admin.execute(text(statement))


@pytest.fixture
async def probe(admin_engine: AsyncEngine) -> AsyncIterator[None]:
    """A database with no ``p7_provision_probe`` role, before and after the test."""
    await _drop_probe_role(admin_engine)
    yield
    await _drop_probe_role(admin_engine)


def _probe_url(db_config: DbConfig, credential: str) -> str:
    url = URL.create(
        "postgresql+asyncpg",
        username=PROBE_ROLE,
        password=credential,
        host=db_config.host,
        port=db_config.port,
        database=db_config.database,
    )
    return url.render_as_string(hide_password=False)


def _admin_url(db_config: DbConfig) -> str:
    return db_config.url(admin=True).render_as_string(hide_password=False)


def _probe_engine(db_config: DbConfig, credential: str) -> AsyncEngine:
    return create_async_engine(_probe_url(db_config, credential), poolclass=NullPool)


# --- the role itself ------------------------------------------------------------------------------


async def test_it_creates_a_role_that_can_log_in_and_has_no_privileges_of_its_own(
    probe: None, db_config: DbConfig, admin_engine: AsyncEngine
) -> None:
    await provision(
        migration_url=_admin_url(db_config), runtime_url=_probe_url(db_config, FIRST_CREDENTIAL)
    )

    async with admin_engine.connect() as admin:
        row = (await admin.execute(text(ROLE_FLAGS))).one()
    assert row.rolcanlogin is True
    assert (row.rolsuper, row.rolcreatedb, row.rolcreaterole, row.rolreplication) == (
        False,
        False,
        False,
        False,
    )


async def test_the_new_role_can_actually_connect(probe: None, db_config: DbConfig) -> None:
    """The grant that is easiest to forget: without CONNECT the password is irrelevant."""
    await provision(
        migration_url=_admin_url(db_config), runtime_url=_probe_url(db_config, FIRST_CREDENTIAL)
    )

    engine = _probe_engine(db_config, FIRST_CREDENTIAL)
    try:
        async with engine.connect() as connection:
            assert (
                await connection.execute(text("SELECT current_user"))
            ).scalar_one() == PROBE_ROLE
    finally:
        await engine.dispose()


async def test_the_new_role_cannot_create_tables(probe: None, db_config: DbConfig) -> None:
    """ADR 011's whole point, reproduced without the init script."""
    await provision(
        migration_url=_admin_url(db_config), runtime_url=_probe_url(db_config, FIRST_CREDENTIAL)
    )

    engine = _probe_engine(db_config, FIRST_CREDENTIAL)
    try:
        async with engine.connect() as connection:
            with pytest.raises(DBAPIError, match="permission denied"):
                await connection.execute(text("CREATE TABLE p7_must_not_exist (id int)"))
    finally:
        await engine.dispose()


# --- the grants, in both directions in time -------------------------------------------------------


async def test_it_covers_tables_that_already_existed(
    probe: None, db_config: DbConfig, admin_engine: AsyncEngine
) -> None:
    """On RDS the migration may well have run first, so the tables are already there."""
    async with admin_engine.begin() as admin:
        await admin.execute(text("DROP TABLE IF EXISTS p7_probe_before"))
        await admin.execute(text("CREATE TABLE p7_probe_before (note text)"))
    try:
        await provision(
            migration_url=_admin_url(db_config), runtime_url=_probe_url(db_config, FIRST_CREDENTIAL)
        )

        engine = _probe_engine(db_config, FIRST_CREDENTIAL)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("INSERT INTO p7_probe_before (note) VALUES ('ok')"))
                assert (
                    await connection.execute(text("SELECT note FROM p7_probe_before"))
                ).scalar_one() == "ok"
        finally:
            await engine.dispose()
    finally:
        async with admin_engine.begin() as admin:
            await admin.execute(text("DROP TABLE IF EXISTS p7_probe_before"))


async def test_it_covers_tables_created_by_a_later_migration(
    probe: None, db_config: DbConfig, admin_engine: AsyncEngine
) -> None:
    """And it may well run first, with every later migration's tables still to come."""
    await provision(
        migration_url=_admin_url(db_config), runtime_url=_probe_url(db_config, FIRST_CREDENTIAL)
    )

    async with admin_engine.begin() as admin:
        await admin.execute(text("DROP TABLE IF EXISTS p7_probe_after"))
        await admin.execute(text("CREATE TABLE p7_probe_after (note text)"))
    try:
        engine = _probe_engine(db_config, FIRST_CREDENTIAL)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("INSERT INTO p7_probe_after (note) VALUES ('ok')"))
                assert (
                    await connection.execute(text("SELECT note FROM p7_probe_after"))
                ).scalar_one() == "ok"
        finally:
            await engine.dispose()
    finally:
        async with admin_engine.begin() as admin:
            await admin.execute(text("DROP TABLE IF EXISTS p7_probe_after"))


# --- running it more than once --------------------------------------------------------------------


async def test_running_it_twice_succeeds_and_leaves_the_role_working(
    probe: None, db_config: DbConfig
) -> None:
    """Every cold start runs this task. The second run must be a no-op, not an error."""
    admin = _admin_url(db_config)
    runtime = _probe_url(db_config, FIRST_CREDENTIAL)

    await provision(migration_url=admin, runtime_url=runtime)
    await provision(migration_url=admin, runtime_url=runtime)

    engine = _probe_engine(db_config, FIRST_CREDENTIAL)
    try:
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await engine.dispose()


async def test_a_rotated_password_replaces_the_old_one(probe: None, db_config: DbConfig) -> None:
    """Raising db_password_version writes a new password into SSM; re-running must apply it."""
    admin = _admin_url(db_config)
    await provision(migration_url=admin, runtime_url=_probe_url(db_config, FIRST_CREDENTIAL))
    await provision(migration_url=admin, runtime_url=_probe_url(db_config, SECOND_CREDENTIAL))

    new = _probe_engine(db_config, SECOND_CREDENTIAL)
    try:
        async with new.connect() as connection:
            assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await new.dispose()

    old = _probe_engine(db_config, FIRST_CREDENTIAL)
    try:
        # asyncpg raises this while opening the connection, so SQLAlchemy never wraps it in a
        # DBAPIError the way it wraps a failing statement. Naming the exception that is actually
        # raised, rather than the one that looked likely.
        with pytest.raises(InvalidPasswordError, match="password authentication failed"):
            async with old.connect() as connection:
                await connection.execute(text("SELECT 1"))
    finally:
        await old.dispose()


async def test_a_role_that_has_been_widened_is_reported_rather_than_ignored(
    probe: None, db_config: DbConfig, admin_engine: AsyncEngine
) -> None:
    """The real drill found that on RDS we cannot always take an attribute back.

    The migration role there is `rds_superuser`, not a superuser, so `ALTER ROLE ... NOSUPERUSER`
    is refused outright. Rather than quietly leaving a widened role in place, the task fails and
    says what is wrong.
    """
    admin = _admin_url(db_config)
    await provision(migration_url=admin, runtime_url=_probe_url(db_config, FIRST_CREDENTIAL))

    async with admin_engine.begin() as connection:
        await connection.execute(text("ALTER ROLE p7_provision_probe WITH CREATEDB"))

    with pytest.raises(RuntimeError, match="create databases"):
        await provision(migration_url=admin, runtime_url=_probe_url(db_config, FIRST_CREDENTIAL))
