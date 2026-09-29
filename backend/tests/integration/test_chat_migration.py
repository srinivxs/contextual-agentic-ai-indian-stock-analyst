"""Revision 0008: conversations and messages, the chat's history (P12).

A conversation belongs to one user and goes when the user goes. A message is the user's question
or the assistant's reply; only a reply has a status (answered, abstained, out_of_scope), and it
must have one. The tokens stored with each reply are the chat's spending record, so they can never
be negative.
"""

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import MakeUser, Migrator

pytestmark = pytest.mark.usefixtures("migrated_db")


async def add_conversation(engine: AsyncEngine, user_id: UUID, title: str = "DemoCo") -> UUID:
    async with engine.begin() as connection:
        found: UUID = (
            await connection.execute(
                text(
                    "INSERT INTO conversations (user_id, title) VALUES (:user_id, :title) "
                    "RETURNING id"
                ),
                {"user_id": user_id, "title": title},
            )
        ).scalar_one()
        return found


def message(conversation: UUID, /, **overrides: Any) -> dict[str, Any]:
    return {
        "conversation_id": conversation,
        "role": "assistant",
        "text": "DemoCo's net profit was ₹110 crore [1].",
        "status": "answered",
        "input_tokens": 1000,
        "output_tokens": 200,
        **overrides,
    }


async def add_message(engine: AsyncEngine, values: dict[str, Any]) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO messages (conversation_id, role, text, status, input_tokens, "
                "output_tokens) VALUES (:conversation_id, :role, :text, :status, :input_tokens, "
                ":output_tokens)"
            ),
            values,
        )


async def count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as connection:
        found: int = (
            await connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
        ).scalar_one()
        return found


async def test_the_runtime_role_can_store_and_read_a_conversation(
    make_user: MakeUser, app_engine: AsyncEngine
) -> None:
    user_id = await make_user()
    conversation = await add_conversation(app_engine, user_id)
    await add_message(app_engine, message(conversation, role="user", status=None, input_tokens=0))
    await add_message(app_engine, message(conversation))

    async with app_engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT c.id, c.created_at, c.updated_at, m.id AS message_id, m.sources, "
                    "m.model, m.created_at AS message_created_at FROM conversations c "
                    "JOIN messages m ON m.conversation_id = c.id WHERE m.role = 'assistant'"
                )
            )
        ).one()
    assert row.id.version == 4  # gen_random_uuid()
    assert row.message_id.version == 4
    assert row.created_at.tzinfo is not None
    assert row.updated_at.tzinfo is not None
    assert row.message_created_at.tzinfo is not None
    assert row.sources in ([], "[]")  # the default: no sources
    assert row.model is None


@pytest.mark.parametrize("status", ["answered", "abstained", "out_of_scope"])
async def test_every_reply_status_is_accepted(
    make_user: MakeUser, admin_engine: AsyncEngine, status: str
) -> None:
    conversation = await add_conversation(admin_engine, await make_user())
    await add_message(admin_engine, message(conversation, status=status))
    assert await count(admin_engine, "messages") == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": None},  # a reply must say what it is: NULL must not slip through the CHECK
        {"status": "guessed"},
        {"role": "user"},  # a question has no status
        {"role": "user", "status": "abstained"},
        {"role": "system", "status": None},
        {"role": "tool"},
        {"text": ""},
        {"text": "x" * 8001},
        {"input_tokens": -1},
        {"output_tokens": -1},
    ],
    ids=[
        "reply-without-status",
        "unknown-status",
        "question-with-status",
        "question-with-abstained",
        "system-role",
        "tool-role",
        "empty-text",
        "text-too-long",
        "negative-input-tokens",
        "negative-output-tokens",
    ],
)
async def test_a_malformed_message_is_refused(
    make_user: MakeUser, admin_engine: AsyncEngine, overrides: dict[str, Any]
) -> None:
    conversation = await add_conversation(admin_engine, await make_user())
    with pytest.raises(DBAPIError):
        await add_message(admin_engine, message(conversation, **overrides))
    assert await count(admin_engine, "messages") == 0


async def test_the_longest_allowed_message_is_accepted(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    conversation = await add_conversation(admin_engine, await make_user())
    await add_message(admin_engine, message(conversation, text="x" * 8000))
    assert await count(admin_engine, "messages") == 1


@pytest.mark.parametrize("title", ["", "x" * 81], ids=["empty", "too-long"])
async def test_a_title_must_be_1_to_80_characters(
    make_user: MakeUser, admin_engine: AsyncEngine, title: str
) -> None:
    user_id = await make_user()
    with pytest.raises(DBAPIError):
        await add_conversation(admin_engine, user_id, title)
    await add_conversation(admin_engine, user_id, "x" * 80)
    assert await count(admin_engine, "conversations") == 1


async def test_a_conversation_needs_a_real_user(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    await make_user()
    with pytest.raises(DBAPIError):
        await add_conversation(admin_engine, UUID(int=1))


async def test_deleting_a_user_removes_their_conversations_and_messages(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    leaving = await make_user()
    staying = await make_user()
    for user_id in (leaving, staying):
        await add_message(admin_engine, message(await add_conversation(admin_engine, user_id)))

    async with admin_engine.begin() as connection:
        await connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": leaving})

    assert (await count(admin_engine, "conversations"), await count(admin_engine, "messages")) == (
        1,
        1,
    )


async def test_deleting_a_conversation_removes_its_messages(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    conversation = await add_conversation(admin_engine, await make_user())
    await add_message(admin_engine, message(conversation))
    async with admin_engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM conversations WHERE id = :id"), {"id": conversation}
        )
    assert await count(admin_engine, "messages") == 0


async def test_the_lookups_have_their_indexes(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as connection:
        names = set(
            (
                await connection.execute(
                    text(
                        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
                        "AND tablename IN ('conversations', 'messages')"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert {"ix_conversations_user_updated", "ix_messages_conversation_created"} <= names


TABLE = '{"title": "DemoCo net profit", "columns": ["Year"], "rows": [["FY2026"]], "source": {}}'


async def test_a_reply_may_carry_a_data_table_and_a_question_may_not(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    conversation = await add_conversation(admin_engine, await make_user())
    insert = text(
        "INSERT INTO messages (conversation_id, role, text, status, data_table) "
        "VALUES (:conversation_id, :role, 'x', :status, CAST(:data_table AS jsonb))"
    )
    async with admin_engine.begin() as connection:
        await connection.execute(
            insert,
            {
                "conversation_id": conversation,
                "role": "assistant",
                "status": "answered",
                "data_table": TABLE,
            },
        )
        await connection.execute(
            insert,
            {
                "conversation_id": conversation,
                "role": "assistant",
                "status": "answered",
                "data_table": None,
            },
        )
        await connection.execute(
            insert,
            {"conversation_id": conversation, "role": "user", "status": None, "data_table": None},
        )
    with pytest.raises(DBAPIError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                insert,
                {
                    "conversation_id": conversation,
                    "role": "user",
                    "status": None,
                    "data_table": TABLE,
                },
            )
    assert await count(admin_engine, "messages") == 3


async def test_older_messages_have_no_data_table(
    make_user: MakeUser, admin_engine: AsyncEngine
) -> None:
    await add_message(
        admin_engine, message(await add_conversation(admin_engine, await make_user()))
    )
    async with admin_engine.connect() as connection:
        found = (await connection.execute(text("SELECT data_table FROM messages"))).scalar_one()
    assert found is None


async def test_downgrading_drops_the_data_table_column(
    migrator: Migrator, admin_engine: AsyncEngine
) -> None:
    await migrator.downgrade("0011")
    try:
        async with admin_engine.connect() as connection:
            columns = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'messages' AND column_name = 'data_table'"
                    )
                )
            ).scalar_one()
        assert columns == 0
    finally:
        await migrator.upgrade("head")


async def test_downgrading_removes_both_tables(
    migrator: Migrator, admin_engine: AsyncEngine
) -> None:
    await migrator.downgrade("0007")
    try:
        async with admin_engine.connect() as connection:
            tables = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                        "AND tablename IN ('conversations', 'messages')"
                    )
                )
            ).scalar_one()
        assert tables == 0
    finally:
        await migrator.upgrade("head")
