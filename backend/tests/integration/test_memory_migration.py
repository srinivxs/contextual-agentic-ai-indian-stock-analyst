"""Revision 0009: investor_profiles, and 'remembered' widened into messages.status (P13).

At most one row per (user, field); the field-specific CHOICES, cardinality, no-NULL and
single-valued rules are all enforced by CHECK constraints, so a bad row can never reach the table
even from a bug in app code.
"""

from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import MakeUser, Migrator

pytestmark = pytest.mark.usefixtures("migrated_db")

INSERT = (
    "INSERT INTO investor_profiles (user_id, field, tags, quote, source) "
    "VALUES (:user_id, :field, :tags, :quote, :source)"
)


def row(user_id: UUID, /, **overrides: Any) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "field": "risk_preference",
        "tags": ["conservative"],
        "quote": "I don't like risk.",
        "source": "chat",
        **overrides,
    }


async def insert(engine: AsyncEngine, values: dict[str, Any]) -> None:
    async with engine.begin() as connection:
        await connection.execute(text(INSERT), values)


async def count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as connection:
        found: int = (
            await connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
        ).scalar_one()
        return found


async def test_a_well_formed_row_of_every_field_is_accepted(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    user_id = await make_user()
    await insert(admin_engine, row(user_id, field="risk_preference", tags=["conservative"]))
    await insert(admin_engine, row(user_id, field="debt_preference", tags=["debt_ok"]))
    await insert(
        admin_engine, row(user_id, field="investment_style", tags=["income", "growth", "quality"])
    )
    await insert(
        admin_engine, row(user_id, field="other_preferences", tags=["long_term", "stability"])
    )
    assert await count(admin_engine, "investor_profiles") == 4


async def test_the_runtime_role_can_read_and_write_its_own_rows(
    make_user: MakeUser, app_engine: AsyncEngine
) -> None:
    user_id = await make_user()
    async with app_engine.begin() as connection:
        await connection.execute(text(INSERT), row(user_id))
        found = (
            await connection.execute(
                text("SELECT field, tags, source FROM investor_profiles WHERE user_id = :id"),
                {"id": user_id},
            )
        ).one()
        assert (found.field, list(found.tags), found.source) == (
            "risk_preference",
            ["conservative"],
            "chat",
        )
        await connection.execute(
            text("UPDATE investor_profiles SET tags = ARRAY['aggressive'] WHERE user_id = :id"),
            {"id": user_id},
        )
        await connection.execute(
            text("DELETE FROM investor_profiles WHERE user_id = :id"), {"id": user_id}
        )
    assert await count(app_engine, "investor_profiles") == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"field": "net_worth"},  # not one of the four fields
        {"tags": ["reckless"]},  # not in risk_preference's CHOICES
        {"field": "debt_preference", "tags": ["avoid_high_debt", "debt_ok"]},  # single-valued
        {"tags": []},  # empty
        {"tags": ["conservative", "moderate", "aggressive", "conservative", "moderate", "x"]},
        {"quote": ""},
        {"quote": "x" * 301},
        {"source": "guessed"},
        {
            "field": "investment_style",
            "tags": ["income", "debt_ok"],
        },  # a debt_preference tag under investment_style
    ],
    ids=[
        "unknown-field",
        "tag-not-in-choices",
        "two-values-for-a-single-valued-field",
        "empty-tags",
        "too-many-tags",
        "empty-quote",
        "quote-too-long",
        "unknown-source",
        "tag-from-the-wrong-field",
    ],
)
async def test_a_malformed_row_is_refused(
    make_user: MakeUser, admin_engine: AsyncEngine, overrides: dict[str, Any]
) -> None:
    user_id = await make_user()
    with pytest.raises(DBAPIError):
        await insert(admin_engine, row(user_id, **overrides))
    assert await count(admin_engine, "investor_profiles") == 0


async def test_a_null_tag_is_refused(make_user: MakeUser, admin_engine: AsyncEngine) -> None:
    user_id = await make_user()
    async with admin_engine.begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "INSERT INTO investor_profiles (user_id, field, tags, quote, source) "
                    "VALUES (:user_id, 'risk_preference', ARRAY['conservative', NULL], "
                    "'quote', 'chat')"
                ),
                {"user_id": user_id},
            )
    assert await count(admin_engine, "investor_profiles") == 0


async def test_a_second_row_for_the_same_field_is_refused_not_merged(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    """The merge rule (a newer statement replaces a field) is the store's job (ON CONFLICT); the
    raw primary key simply refuses a second row for the same (user, field)."""
    user_id = await make_user()
    await insert(admin_engine, row(user_id))
    with pytest.raises(DBAPIError):
        await insert(admin_engine, row(user_id, quote="Actually I'm aggressive."))
    assert await count(admin_engine, "investor_profiles") == 1


async def test_a_profile_needs_a_real_user(admin_engine: AsyncEngine) -> None:
    with pytest.raises(DBAPIError):
        await insert(admin_engine, row(uuid4()))


async def test_deleting_a_user_removes_their_profile(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    leaving = await make_user()
    staying = await make_user()
    await insert(admin_engine, row(leaving))
    await insert(admin_engine, row(staying, field="debt_preference", tags=["debt_ok"]))

    async with admin_engine.begin() as connection:
        await connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": leaving})

    assert await count(admin_engine, "investor_profiles") == 1


# --- messages.status widened to accept 'remembered' ----------------------------------------------


async def add_conversation(engine: AsyncEngine, user_id: UUID) -> UUID:
    async with engine.begin() as connection:
        found: UUID = (
            await connection.execute(
                text(
                    "INSERT INTO conversations (user_id, title) VALUES (:user_id, 'DemoCo') "
                    "RETURNING id"
                ),
                {"user_id": user_id},
            )
        ).scalar_one()
        return found


async def add_message(
    engine: AsyncEngine, conversation: UUID, role: str, status: str | None
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO messages (conversation_id, role, text, status) "
                "VALUES (:id, :role, 'text', :status)"
            ),
            {"id": conversation, "role": role, "status": status},
        )


async def test_an_assistant_message_may_be_remembered(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    conversation = await add_conversation(admin_engine, await make_user())
    await add_message(admin_engine, conversation, "assistant", "remembered")
    assert await count(admin_engine, "messages") == 1


async def test_a_user_message_may_not_be_remembered(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    conversation = await add_conversation(admin_engine, await make_user())
    with pytest.raises(DBAPIError):
        await add_message(admin_engine, conversation, "user", "remembered")
    assert await count(admin_engine, "messages") == 0


async def test_downgrading_deletes_remembered_messages_and_restores_the_old_check(
    make_user: MakeUser, migrator: Migrator, admin_engine: AsyncEngine
) -> None:
    conversation = await add_conversation(admin_engine, await make_user())
    await add_message(admin_engine, conversation, "assistant", "remembered")
    await add_message(admin_engine, conversation, "assistant", "answered")

    await migrator.downgrade("0008")
    try:
        async with admin_engine.connect() as connection:
            tables = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                        "AND tablename = 'investor_profiles'"
                    )
                )
            ).scalar_one()
            remaining = (
                (await connection.execute(text("SELECT status FROM messages"))).scalars().all()
            )
        assert tables == 0
        assert remaining == ["answered"]

        with pytest.raises(DBAPIError):
            await add_message(admin_engine, conversation, "assistant", "remembered")
    finally:
        await migrator.upgrade("head")
