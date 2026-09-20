"""Session creation, lookup and deletion against the real database.

The functions never commit: the caller decides where the transaction ends.
"""

import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.auth.sessions import (
    CurrentUser,
    create_session,
    delete_session,
    get_current_user,
    hash_session_token,
)
from tests.integration.auth_helpers import (
    SEVEN_DAYS,
    age_all_sessions,
    count_sessions,
    open_session,
)
from tests.integration.conftest import MakeUser

Factory = async_sessionmaker[AsyncSession]


async def lookup(factory: Factory, value: str) -> CurrentUser | None:
    async with factory() as db:
        return await get_current_user(db, value)


async def test_only_the_hash_is_stored_never_the_token(
    make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    user_id = await make_user()
    value = await open_session(session_factory, user_id)

    async with admin_engine.connect() as connection:
        row = (await connection.execute(text("SELECT token_hash FROM sessions"))).one()
    # Recomputed here with hashlib, NOT with the application's own function, so a broken hash
    # function cannot fool this test.
    assert bytes(row.token_hash) == hashlib.sha256(value.encode("ascii")).digest()
    assert bytes(row.token_hash) == hash_session_token(value)
    assert value.encode("ascii") not in bytes(row.token_hash)
    async with admin_engine.connect() as connection:
        dump = (await connection.execute(text("SELECT sessions::text FROM sessions"))).scalar_one()
    assert value not in dump  # the raw token appears nowhere in the stored row


async def test_the_lifetime_is_fixed_at_creation(
    make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await open_session(session_factory, await make_user(), SEVEN_DAYS)
    async with admin_engine.connect() as connection:
        row = (await connection.execute(text("SELECT created_at, expires_at FROM sessions"))).one()
    assert row.expires_at - row.created_at == timedelta(days=7)


async def test_a_valid_token_finds_its_user(make_user: MakeUser, session_factory: Factory) -> None:
    user_id = await make_user(email="ada@example.test")
    value = await open_session(session_factory, user_id)

    assert await lookup(session_factory, value) == CurrentUser(id=user_id, email="ada@example.test")


@pytest.mark.parametrize(
    "guess",
    ["", "x", "A" * 43, "not-a-real-token"],
    ids=["empty", "short", "well-formed-but-unknown", "text"],
)
async def test_an_unknown_token_finds_nobody(
    make_user: MakeUser, session_factory: Factory, guess: str
) -> None:
    await open_session(session_factory, await make_user())
    assert await lookup(session_factory, guess) is None


async def test_a_token_that_differs_by_one_character_finds_nobody(
    make_user: MakeUser, session_factory: Factory
) -> None:
    value = await open_session(session_factory, await make_user())
    altered = value[:-1] + ("A" if value[-1] != "A" else "B")
    assert await lookup(session_factory, altered) is None
    assert await lookup(session_factory, value) is not None  # while the real one still works


async def test_an_expired_session_finds_nobody(
    make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    value = await open_session(session_factory, await make_user())
    assert await lookup(session_factory, value) is not None

    await age_all_sessions(admin_engine)

    assert await lookup(session_factory, value) is None


async def test_a_session_expiring_soon_still_works(
    make_user: MakeUser, session_factory: Factory
) -> None:
    value = await open_session(session_factory, await make_user(), timedelta(minutes=1))
    assert await lookup(session_factory, value) is not None


async def test_deleting_removes_the_session_and_deleting_again_is_harmless(
    make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    value = await open_session(session_factory, await make_user())

    for _ in range(2):  # the second call finds nothing to delete and must not fail
        async with session_factory() as db:
            await delete_session(db, value)
            await db.commit()

    assert await lookup(session_factory, value) is None
    assert await count_sessions(admin_engine) == 0


async def test_deleting_an_unknown_token_is_harmless(
    make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await open_session(session_factory, await make_user())
    async with session_factory() as db:
        await delete_session(db, "no-such-token")
        await db.commit()
    assert await count_sessions(admin_engine) == 1


async def test_a_user_can_be_signed_in_on_two_devices_and_logging_out_one_keeps_the_other(
    make_user: MakeUser, session_factory: Factory
) -> None:
    user_id = await make_user()
    laptop = await open_session(session_factory, user_id)
    phone = await open_session(session_factory, user_id)
    assert laptop != phone

    async with session_factory() as db:
        await delete_session(db, laptop)
        await db.commit()

    assert await lookup(session_factory, laptop) is None
    assert await lookup(session_factory, phone) is not None


async def test_each_user_only_finds_themselves(
    make_user: MakeUser, session_factory: Factory
) -> None:
    ada = await make_user(email="ada@example.test")
    grace = await make_user(email="grace@example.test")
    ada_token = await open_session(session_factory, ada)
    grace_token = await open_session(session_factory, grace)

    assert (await lookup(session_factory, ada_token)) == CurrentUser(ada, "ada@example.test")
    assert (await lookup(session_factory, grace_token)) == CurrentUser(grace, "grace@example.test")


async def test_deleting_a_user_removes_their_sessions(
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
) -> None:
    user_id = await make_user()
    await open_session(session_factory, user_id)
    await open_session(session_factory, user_id)

    async with admin_engine.begin() as connection:
        await connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})

    assert await count_sessions(admin_engine) == 0


async def test_creating_a_session_does_not_commit_on_its_own(
    make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """Transaction boundaries stay visible: a rolled-back login leaves nothing behind."""
    user_id = await make_user()
    async with session_factory() as db:
        await create_session(db, user_id=user_id, lifetime=SEVEN_DAYS)
        await db.rollback()
    assert await count_sessions(admin_engine) == 0
