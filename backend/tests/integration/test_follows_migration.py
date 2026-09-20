"""Revision 0003: the user_follows table, checked in the real database."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import MakeUser

pytestmark = pytest.mark.usefixtures("migrated_db")

COLUMNS = """
    SELECT column_name, data_type, is_nullable, column_default IS NOT NULL AS has_default
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'user_follows' ORDER BY ordinal_position
"""

CONSTRAINTS = """
    SELECT conname, contype::text, pg_get_constraintdef(oid) FROM pg_constraint
    WHERE conrelid = 'user_follows'::regclass ORDER BY conname
"""

INSERT_FOLLOW = "INSERT INTO user_follows (user_id, stock_id) VALUES (:user_id, :stock_id)"


async def stock_id(engine: AsyncEngine, symbol: str = "TCS") -> int:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT id FROM stocks WHERE symbol = :symbol"), {"symbol": symbol}
        )
        found: int = result.scalar_one()
        return found


async def test_the_table_has_the_agreed_columns_and_only_those(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as connection:
        columns = [tuple(r) for r in await connection.execute(text(COLUMNS))]
    assert columns == [
        ("user_id", "uuid", "NO", False),
        ("stock_id", "bigint", "NO", False),
        ("created_at", "timestamp with time zone", "NO", True),
    ]


async def test_the_pair_is_the_primary_key_and_the_constraints_are_named(
    admin_engine: AsyncEngine,
) -> None:
    async with admin_engine.connect() as connection:
        constraints = {r[0]: (r[1], r[2]) for r in await connection.execute(text(CONSTRAINTS))}

    assert constraints["pk_user_follows"] == ("p", "PRIMARY KEY (user_id, stock_id)")
    assert set(constraints) == {
        "pk_user_follows",
        "fk_user_follows_user_id_users",
        "fk_user_follows_stock_id_stocks",
    }


async def test_following_the_same_stock_twice_is_a_constraint_violation(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    """The database, not the application, is what makes a duplicate impossible."""
    user_id = await make_user()
    tcs = await stock_id(admin_engine)
    async with admin_engine.begin() as connection:
        await connection.execute(text(INSERT_FOLLOW), {"user_id": user_id, "stock_id": tcs})
    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as connection:
            await connection.execute(text(INSERT_FOLLOW), {"user_id": user_id, "stock_id": tcs})


async def test_a_follow_needs_a_real_user_and_a_real_stock(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    user_id = await make_user()
    tcs = await stock_id(admin_engine)
    for values in (
        {"user_id": "00000000-0000-0000-0000-000000000000", "stock_id": tcs},
        {"user_id": user_id, "stock_id": 999_999},
    ):
        with pytest.raises(IntegrityError):
            async with admin_engine.begin() as connection:
                await connection.execute(text(INSERT_FOLLOW), values)


async def test_created_at_defaults_to_now(make_user: MakeUser, admin_engine: AsyncEngine) -> None:
    user_id = await make_user()
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(INSERT_FOLLOW), {"user_id": user_id, "stock_id": await stock_id(admin_engine)}
        )
        age = (
            await connection.execute(
                text("SELECT now() - created_at < interval '1 minute' FROM user_follows")
            )
        ).scalar_one()
    assert age is True


async def test_deleting_a_user_removes_their_follows(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    user_id = await make_user()
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(INSERT_FOLLOW), {"user_id": user_id, "stock_id": await stock_id(admin_engine)}
        )
        await connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        left = (await connection.execute(text("SELECT count(*) FROM user_follows"))).scalar_one()
    assert left == 0


async def test_a_followed_stock_cannot_be_deleted_from_under_its_followers(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    """The universe is fixed (ADR 007): removing a stock must be a deliberate migration, not a side
    effect that silently erases people's follows."""
    user_id = await make_user()
    tcs = await stock_id(admin_engine)
    async with admin_engine.begin() as connection:
        await connection.execute(text(INSERT_FOLLOW), {"user_id": user_id, "stock_id": tcs})
    with pytest.raises(IntegrityError):
        async with admin_engine.begin() as connection:
            await connection.execute(text("DELETE FROM stocks WHERE id = :id"), {"id": tcs})


async def test_the_runtime_role_can_use_the_table_but_not_change_it(
    make_user: MakeUser, admin_engine: AsyncEngine, app_engine: AsyncEngine
) -> None:
    user_id = await make_user()
    tcs = await stock_id(admin_engine)
    async with app_engine.begin() as connection:
        await connection.execute(text(INSERT_FOLLOW), {"user_id": user_id, "stock_id": tcs})
        count = (await connection.execute(text("SELECT count(*) FROM user_follows"))).scalar_one()
        assert count == 1
        await connection.execute(text("DELETE FROM user_follows"))
    for statement in (
        "ALTER TABLE user_follows ADD COLUMN sneaky int",
        "DROP TABLE user_follows",
        "TRUNCATE user_follows",
    ):
        async with app_engine.connect() as connection:
            with pytest.raises(DBAPIError):
                await connection.execute(text(statement))
