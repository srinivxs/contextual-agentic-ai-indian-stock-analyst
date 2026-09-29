"""Revision 0002: the users and sessions tables, checked in the real database."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import MakeUser

pytestmark = pytest.mark.usefixtures("migrated_db")

COLUMNS = """
    SELECT column_name, data_type, is_nullable, column_default IS NOT NULL AS has_default
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = :table ORDER BY ordinal_position
"""

CONSTRAINTS = """
    SELECT conname FROM pg_constraint WHERE conrelid = CAST(:table AS regclass) ORDER BY conname
"""

INDEXES = "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = :table"

INSERT_SESSION = (
    "INSERT INTO sessions (user_id, token_hash, expires_at) "
    "VALUES (:user_id, :hash, now() + interval '7 days')"
)


async def rows(engine: AsyncEngine, sql: str, table: str) -> list[tuple[object, ...]]:
    async with engine.connect() as connection:
        return [tuple(r) for r in await connection.execute(text(sql), {"table": table})]


async def test_the_migration_is_at_head(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as connection:
        version = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar()
    assert version == "0011"  # the head moves on as later phases add migrations


async def test_users_has_the_agreed_columns_and_only_those(admin_engine: AsyncEngine) -> None:
    """`google_sub` and `email` are all we keep of Google's data: no name, picture or tokens."""
    assert await rows(admin_engine, COLUMNS, "users") == [
        ("id", "uuid", "NO", True),
        ("google_sub", "text", "NO", False),
        ("email", "text", "NO", False),
        ("created_at", "timestamp with time zone", "NO", True),
        ("last_login_at", "timestamp with time zone", "NO", True),
    ]


async def test_sessions_has_the_agreed_columns_and_only_those(admin_engine: AsyncEngine) -> None:
    assert await rows(admin_engine, COLUMNS, "sessions") == [
        ("id", "uuid", "NO", True),
        ("user_id", "uuid", "NO", False),
        ("token_hash", "bytea", "NO", False),  # the hash; the raw token has no column at all
        ("created_at", "timestamp with time zone", "NO", True),
        ("expires_at", "timestamp with time zone", "NO", False),
    ]


async def test_constraints_and_indexes_have_explicit_names(admin_engine: AsyncEngine) -> None:
    users = [r[0] for r in await rows(admin_engine, CONSTRAINTS, "users")]
    sessions = [r[0] for r in await rows(admin_engine, CONSTRAINTS, "sessions")]
    assert users == [
        "ck_users_email_not_blank",
        "ck_users_google_sub_not_blank",
        "pk_users",
        "uq_users_google_sub",
    ]
    assert sessions == [
        "ck_sessions_expires_after_created",
        "ck_sessions_token_hash_length",
        "fk_sessions_user_id_users",
        "pk_sessions",
        "uq_sessions_token_hash",
    ]
    session_indexes = {r[0] for r in await rows(admin_engine, INDEXES, "sessions")}
    assert {"ix_sessions_user_id", "ix_sessions_expires_at"} <= session_indexes


async def test_google_sub_is_the_identity_and_email_is_not_unique(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    await make_user(sub="google-sub-1", email="same@example.test")
    await make_user(sub="google-sub-2", email="same@example.test")  # same email, different person

    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO users (google_sub, email) VALUES ('google-sub-1', 'other@x.test')"
                )
            )


@pytest.mark.parametrize(
    "values",
    [("", "a@example.test"), ("sub", ""), (" ", "a@example.test"), ("sub", "   ")],
    ids=["empty-sub", "empty-email", "blank-sub", "blank-email"],
)
async def test_blank_identity_values_are_rejected(
    make_user: MakeUser, admin_engine: AsyncEngine, values: tuple[str, str]
) -> None:
    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                text("INSERT INTO users (google_sub, email) VALUES (:sub, :email)"),
                {"sub": values[0], "email": values[1]},
            )


async def test_ids_and_timestamps_default_to_uuids_and_now(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    await make_user()
    async with admin_engine.connect() as connection:
        row = (
            await connection.execute(text("SELECT id, created_at, last_login_at FROM users"))
        ).one()
    assert row.id.version == 4  # gen_random_uuid()
    assert row.created_at.tzinfo is not None
    assert row.last_login_at.tzinfo is not None


async def test_a_token_hash_must_be_32_bytes_and_unique(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    user_id = await make_user()
    good = b"\x01" * 32
    async with admin_engine.begin() as connection:
        await connection.execute(text(INSERT_SESSION), {"user_id": user_id, "hash": good})

    for bad in (b"\x02" * 31, b"\x02" * 33, b""):  # not a SHA-256 digest
        with pytest.raises(IntegrityError):
            async with admin_engine.begin() as connection:
                await connection.execute(text(INSERT_SESSION), {"user_id": user_id, "hash": bad})
    with pytest.raises(IntegrityError):  # the same hash twice
        async with admin_engine.begin() as connection:
            await connection.execute(text(INSERT_SESSION), {"user_id": user_id, "hash": good})


async def test_a_session_cannot_expire_before_it_was_created(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    user_id = await make_user()
    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO sessions (user_id, token_hash, expires_at) "
                    "VALUES (:user_id, :hash, now() - interval '1 second')"
                ),
                {"user_id": user_id, "hash": b"\x03" * 32},
            )


async def test_a_session_needs_a_real_user(admin_engine: AsyncEngine, make_user: MakeUser) -> None:
    await make_user()
    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(INSERT_SESSION), {"user_id": uuid4(), "hash": b"\x04" * 32}
            )


async def test_the_runtime_role_can_use_the_tables_but_not_change_them(
    make_user: MakeUser, app_engine: AsyncEngine
) -> None:
    user_id = await make_user()
    async with app_engine.begin() as connection:
        await connection.execute(text(INSERT_SESSION), {"user_id": user_id, "hash": b"\x05" * 32})
        count = (await connection.execute(text("SELECT count(*) FROM sessions"))).scalar_one()
        assert count == 1
        await connection.execute(text("DELETE FROM sessions"))
    for statement in (
        "ALTER TABLE users ADD COLUMN sneaky int",
        "DROP TABLE sessions",
        "TRUNCATE users CASCADE",
        "CREATE TABLE follows (id int)",  # P5's table: DDL is not the runtime role's job
    ):
        async with app_engine.connect() as connection:
            with pytest.raises(DBAPIError):
                await connection.execute(text(statement))


async def test_no_other_application_tables_exist_yet(admin_engine: AsyncEngine) -> None:
    """Scope guard: documents arrived in P9, embeddings in P10, facts and events in P11,
    conversations and messages in P12, the investor profile in P13,
    the feed in P15."""
    async with admin_engine.connect() as connection:
        tables = (
            (
                await connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")
                )
            )
            .scalars()
            .all()
        )
    assert tables == [
        "alembic_version", "chunks", "conversations", "document_pages", "documents",
        "embeddings", "events", "extraction_calls", "facts", "feed_items",
        "feed_state", "investor_profiles", "jobs", "messages", "price_days", "prices",
        "sessions", "stocks", "user_follows", "users",
    ]  # fmt: skip
