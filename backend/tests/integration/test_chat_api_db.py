"""The chat's HTTP api and its storage, against the real database (P12).

    POST /api/v1/chat/messages                       ask; answered, stored, returned
    GET  /api/v1/chat/conversations                  your conversations, newest first
    GET  /api/v1/chat/conversations/{id}             one of them, with its messages oldest first

The engine is a fake (tests/fake_chat.py): these tests decide what it answers and check what the
api handed it. What is checked here: each exchange is stored with its sources and tokens, a
follow-up carries the history, nobody reaches another user's conversation, and nothing is stored
(or spent) when the chat is switched off, the spending cap is used up, or the engine fails.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.chat.contract import ABSTAIN_TEXT, Choice, DataTable, Reply, Turn
from app.chat.store import (
    add_exchange,
    chat_spent_usd,
    conversation_title,
    create_conversation,
    get_conversation,
    history,
    list_conversations,
    owns_conversation,
)
from tests.fake_chat import DEMO_SOURCE, FakeChatEngine, abstained, answered, remembered
from tests.helpers import running_app
from tests.integration.auth_helpers import open_session
from tests.integration.conftest import DbConfig, MakeUser

Factory = async_sessionmaker[AsyncSession]
ORIGIN = {"Origin": "http://localhost:8000"}
MESSAGES = "/api/v1/chat/messages"
CONVERSATIONS = "/api/v1/chat/conversations"
QUESTION = "What was DemoCo's net profit in FY2026?"


async def sign_in(make_user: MakeUser, session_factory: Factory) -> tuple[UUID, dict[str, str]]:
    user_id = await make_user()
    token = await open_session(session_factory, user_id)
    return user_id, {"Cookie": f"session={token}", **ORIGIN}


@asynccontextmanager
async def chat_app(
    db_config: DbConfig, engine: FakeChatEngine | None, **overrides: Any
) -> AsyncIterator[httpx.AsyncClient]:
    """The real app, with the fake engine where P12's engine will be wired."""
    async with running_app(db_config.settings(**overrides)) as (app, client):
        app.state.chat_engine = engine
        yield client


async def ask(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    question: str = QUESTION,
    conversation_id: str | None = None,
) -> httpx.Response:
    return await client.post(
        MESSAGES,
        json={"question": question, "conversation_id": conversation_id},
        headers=headers,
    )


async def scalar(engine: AsyncEngine, sql: str) -> Any:
    async with engine.connect() as connection:
        return (await connection.execute(text(sql))).scalar_one()


async def counts(engine: AsyncEngine) -> tuple[int, int]:
    return (
        await scalar(engine, "SELECT count(*) FROM conversations"),
        await scalar(engine, "SELECT count(*) FROM messages"),
    )


async def spend(engine: AsyncEngine, user_id: UUID, input_tokens: int, output_tokens: int) -> None:
    """An earlier answer that used this many tokens (the chat's spending record)."""
    async with engine.begin() as connection:
        conversation = (
            await connection.execute(
                text(
                    "INSERT INTO conversations (user_id, title) VALUES (:user_id, 'Earlier') "
                    "RETURNING id"
                ),
                {"user_id": user_id},
            )
        ).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO messages (conversation_id, role, text, status, input_tokens, "
                "output_tokens) VALUES (:id, 'assistant', 'Earlier answer', 'answered', :i, :o)"
            ),
            {"id": conversation, "i": input_tokens, "o": output_tokens},
        )


# --- asking -------------------------------------------------------------------------------------


async def test_an_answer_is_stored_with_its_sources_and_tokens_and_returned(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    user_id, headers = await sign_in(make_user, session_factory)
    engine = FakeChatEngine()

    async with chat_app(db_config, engine) as client:
        response = await ask(client, headers)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    conversation_id = UUID(body["conversation_id"])
    question, answer = body["question"], body["answer"]
    assert (question["role"], question["text"], question["status"], question["sources"]) == (
        "user",
        QUESTION,
        None,
        [],
    )
    assert (answer["role"], answer["text"], answer["status"]) == (
        "assistant",
        "DemoCo's net profit was ₹110 crore in FY2026 [1].",
        "answered",
    )
    assert answer["sources"] == [
        {
            "marker": 1,
            "source": "filing",
            "label": DEMO_SOURCE.label,
            "url": DEMO_SOURCE.url,
            "quote": DEMO_SOURCE.quote,
        }
    ]
    assert set(question) == {
        *("id", "role", "text", "status", "sources", "table", "choices", "created_at"),
    }
    assert question["table"] is None
    assert answer["table"] is None
    assert UUID(question["id"]) != UUID(answer["id"])
    assert question["created_at"] <= answer["created_at"]
    assert engine.calls == [(QUESTION, [], user_id)]

    async with admin_engine.connect() as connection:
        conversation = (
            await connection.execute(text("SELECT id, user_id, title FROM conversations"))
        ).one()
        stored = (
            await connection.execute(
                text(
                    "SELECT role, status, model, input_tokens, output_tokens, sources::text "
                    "FROM messages ORDER BY created_at, role = 'assistant'"
                )
            )
        ).all()
    assert tuple(conversation) == (conversation_id, user_id, QUESTION)
    assert [tuple(row[:5]) for row in stored] == [
        ("user", None, None, 0, 0),
        ("assistant", "answered", "fake-llm", 1000, 200),
    ]
    assert '"marker": 1' in stored[1][5]


DEMO_TABLE = DataTable(
    title="DemoCo net profit (consolidated, ₹ crore)",
    columns=("Year", "Net profit (₹ crore)", "Change"),
    rows=(("FY2026", "1,10", "+10.0%"), ("FY2025", "100", "")),
    source_label="screener.in · consolidated, full years",
    source_url="https://www.screener.in/company/DEMO/consolidated/",
)
TABLE_JSON = {
    "title": DEMO_TABLE.title,
    "columns": ["Year", "Net profit (₹ crore)", "Change"],
    "rows": [["FY2026", "1,10", "+10.0%"], ["FY2025", "100", ""]],
    "source": {
        "label": "screener.in · consolidated, full years",
        "url": "https://www.screener.in/company/DEMO/consolidated/",
    },
}


async def test_a_replys_table_is_stored_returned_and_read_back_in_the_same_shape(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    engine = FakeChatEngine([answered(table=DEMO_TABLE)])

    async with chat_app(db_config, engine) as client:
        posted = (await ask(client, headers)).json()
        listed = (
            await client.get(f"{CONVERSATIONS}/{posted['conversation_id']}", headers=headers)
        ).json()

    assert posted["answer"]["table"] == TABLE_JSON
    assert posted["question"]["table"] is None
    assert [m["table"] for m in listed["messages"]] == [None, TABLE_JSON]
    stored = await scalar(admin_engine, "SELECT data_table FROM messages WHERE role = 'assistant'")
    assert stored == TABLE_JSON


async def test_a_message_stored_without_a_table_returns_null(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """An older row (before migration 0012) has no data_table."""
    user_id, headers = await sign_in(make_user, session_factory)
    await spend(admin_engine, user_id, 1, 1)

    async with chat_app(db_config, None) as client:
        [item] = (await client.get(CONVERSATIONS, headers=headers)).json()["items"]
        listed = (await client.get(f"{CONVERSATIONS}/{item['id']}", headers=headers)).json()

    assert [m["table"] for m in listed["messages"]] == [None]


async def test_an_abstention_is_stored_as_one(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    async with chat_app(db_config, FakeChatEngine([abstained()])) as client:
        answer = (await ask(client, headers)).json()["answer"]

    assert (answer["text"], answer["status"], answer["sources"]) == (ABSTAIN_TEXT, "abstained", [])
    assert await scalar(admin_engine, "SELECT model IS NULL FROM messages WHERE role = 'assistant'")


async def test_a_remembered_reply_is_stored_and_listed(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """P13: a message that only states a preference is saved with no LLM call, and the reply's
    status ("remembered") is stored and comes back exactly like any other reply status."""
    _, headers = await sign_in(make_user, session_factory)
    reply = remembered("Got it, noted as conservative.")
    async with chat_app(db_config, FakeChatEngine([reply])) as client:
        response = await ask(client, headers, "I'm a conservative investor.")
        conversation_id = response.json()["conversation_id"]
        read = await client.get(f"{CONVERSATIONS}/{conversation_id}", headers=headers)

    answer = response.json()["answer"]
    assert (answer["text"], answer["status"], answer["sources"]) == (
        "Got it, noted as conservative.",
        "remembered",
        [],
    )
    stored_status = await scalar(
        admin_engine, "SELECT status FROM messages WHERE role = 'assistant'"
    )
    assert stored_status == "remembered"
    assert [m["status"] for m in read.json()["messages"] if m["role"] == "assistant"] == [
        "remembered"
    ]


async def test_the_question_is_trimmed_and_the_title_is_its_first_80_characters(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    engine = FakeChatEngine()
    long = "  How did DemoCo's\n\n   net profit   change " + "and why " * 20 + "  "

    async with chat_app(db_config, engine) as client:
        body = (await ask(client, headers, long)).json()

    assert body["question"]["text"] == long.strip()
    assert engine.calls[0][0] == long.strip()
    title = await scalar(admin_engine, "SELECT title FROM conversations")
    assert title == " ".join(long.split())[:80]
    assert len(title) == 80


async def test_a_follow_up_passes_the_history_oldest_first(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    engine = FakeChatEngine(lambda question, _: answered(f"About: {question}"))

    async with chat_app(db_config, engine) as client:
        first = (await ask(client, headers)).json()
        conversation_id = first["conversation_id"]
        second = await ask(client, headers, "And the year before?", conversation_id)
        third = await ask(client, headers, "And its dividend?", conversation_id)
        read = await client.get(f"{CONVERSATIONS}/{conversation_id}", headers=headers)

    assert (second.status_code, third.status_code) == (200, 200)
    assert second.json()["conversation_id"] == conversation_id
    assert engine.calls[1][1] == [
        Turn(role="user", text=QUESTION),
        Turn(role="assistant", text=f"About: {QUESTION}", sources=(DEMO_SOURCE,)),
    ]
    assert [turn.text for turn in engine.calls[2][1]] == [
        QUESTION,
        f"About: {QUESTION}",
        "And the year before?",
        "About: And the year before?",
    ]
    assert await counts(admin_engine) == (1, 6)

    assert read.status_code == 200
    assert read.headers["cache-control"] == "no-store"
    conversation = read.json()
    assert conversation["id"] == conversation_id
    assert conversation["title"] == QUESTION
    assert conversation["updated_at"] > conversation["created_at"]  # bumped by each exchange
    assert [(m["role"], m["text"]) for m in conversation["messages"]] == [
        ("user", QUESTION),
        ("assistant", f"About: {QUESTION}"),
        ("user", "And the year before?"),
        ("assistant", "About: And the year before?"),
        ("user", "And its dividend?"),
        ("assistant", "About: And its dividend?"),
    ]
    assert conversation["messages"][1]["sources"][0]["marker"] == 1


async def test_without_a_conversation_id_each_question_starts_a_new_conversation(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    engine = FakeChatEngine()
    async with chat_app(db_config, engine) as client:
        first = (await ask(client, headers, "First question about DemoCo")).json()
        second = (await ask(client, headers, "Second question about DemoCo")).json()
        listed = await client.get(CONVERSATIONS, headers=headers)

    assert first["conversation_id"] != second["conversation_id"]
    assert engine.calls[1][1] == []  # a new conversation has no history
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    items = listed.json()["items"]
    assert [item["id"] for item in items] == [second["conversation_id"], first["conversation_id"]]
    assert set(items[0]) == {"id", "title", "created_at", "updated_at"}
    assert items[0]["title"] == "Second question about DemoCo"


# --- whose conversation -------------------------------------------------------------------------


async def test_another_users_conversation_is_not_found_and_never_reaches_the_engine(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    owner_id = await make_user()
    owner = {"Cookie": f"session={await open_session(session_factory, owner_id)}", **ORIGIN}
    intruder_id = await make_user()
    intruder = {"Cookie": f"session={await open_session(session_factory, intruder_id)}", **ORIGIN}
    engine = FakeChatEngine()

    async with chat_app(db_config, engine) as client:
        theirs = (await ask(client, owner)).json()["conversation_id"]
        responses = [
            await client.get(f"{CONVERSATIONS}/{theirs}", headers=intruder),
            await client.get(f"{CONVERSATIONS}/{uuid4()}", headers=intruder),
            await ask(client, intruder, "Tell me their history", theirs),
            await ask(client, intruder, "Tell me anything", str(uuid4())),
        ]
        listed = (await client.get(CONVERSATIONS, headers=intruder)).json()

    # The same answer whether it exists or not: nothing reveals someone else's conversation.
    assert [r.status_code for r in responses] == [404, 404, 404, 404]
    assert {r.json()["error"]["message"] for r in responses} == {"No such conversation"}
    assert {r.json()["error"]["code"] for r in responses} == {"not_found"}
    assert all(r.headers["cache-control"] == "no-store" for r in responses)
    assert len(engine.calls) == 1  # only the owner's question
    assert listed == {"items": []}
    assert await counts(admin_engine) == (1, 2)


async def test_the_list_holds_at_most_20_of_your_own_newest_first(
    make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    mine = await make_user()
    theirs = await make_user()
    async with admin_engine.begin() as connection:
        for minutes in range(25):
            await connection.execute(
                text(
                    "INSERT INTO conversations (user_id, title, created_at, updated_at) VALUES "
                    "(:user_id, :title, now() - interval '1 day', "
                    "now() - make_interval(mins => :minutes))"
                ),
                {"user_id": mine, "title": f"Mine {minutes}", "minutes": minutes},
            )
        await connection.execute(
            text("INSERT INTO conversations (user_id, title) VALUES (:user_id, 'Theirs')"),
            {"user_id": theirs},
        )

    async with session_factory() as db:
        listed = await list_conversations(db, mine)
        three = await list_conversations(db, mine, limit=3)

    assert [c.title for c in listed] == [f"Mine {n}" for n in range(20)]
    assert [c.title for c in three] == ["Mine 0", "Mine 1", "Mine 2"]
    assert all(c.messages == [] for c in listed)


# --- nothing stored, nothing spent --------------------------------------------------------------


async def test_a_switched_off_chat_refuses_and_stores_nothing(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    async with chat_app(db_config, None) as client:
        response = await ask(client, headers)
        still_readable = await client.get(CONVERSATIONS, headers=headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"
    assert response.json()["error"]["message"] == "Chat is switched off"
    assert still_readable.status_code == 200  # earlier conversations stay readable
    assert await counts(admin_engine) == (0, 0)


async def test_validation_comes_before_the_switch(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    async with chat_app(db_config, None) as client:
        response = await ask(client, headers, "  ")
    assert response.status_code == 422


async def test_the_switch_comes_before_the_spending_cap(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    async with chat_app(db_config, None, chat_budget_usd=Decimal("0")) as client:
        response = await ask(client, headers)
    assert response.status_code == 409


async def test_a_used_up_spending_cap_refuses_before_the_engine_is_called(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """1,000,000 input and 100,000 output tokens at $0.35 and $2.95 per million: $0.645."""
    user_id, headers = await sign_in(make_user, session_factory)
    await spend(admin_engine, user_id, 1_000_000, 100_000)
    engine = FakeChatEngine()

    async with chat_app(db_config, engine, chat_budget_usd=Decimal("0.64")) as client:
        refused = await ask(client, headers)
    async with chat_app(db_config, engine, chat_budget_usd=Decimal("0.65")) as client:
        allowed = await ask(client, headers)

    assert refused.status_code == 503
    assert refused.json()["error"] == {
        "code": "unavailable",
        "message": "The chat's spending cap is used up",
        "request_id": refused.headers["x-request-id"],
    }
    assert allowed.status_code == 200
    assert len(engine.calls) == 1  # only the allowed question reached the engine


async def test_a_zero_cap_means_no_questions_at_all(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    engine = FakeChatEngine()
    async with chat_app(db_config, engine, chat_budget_usd=Decimal("0")) as client:
        response = await ask(client, headers)
    assert response.status_code == 503
    assert engine.calls == []


async def test_an_engine_failure_stores_nothing_and_logs_only_the_error_type(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    private_question = "What is DemoCo's secret plan for FY2027?"
    failing = FakeChatEngine(error=RuntimeError("Bedrock said: throttled, prompt was ..."))

    async with chat_app(db_config, FakeChatEngine()) as client:
        kept = (await ask(client, headers)).json()["conversation_id"]
    with caplog.at_level(logging.DEBUG):
        async with chat_app(db_config, failing) as client:
            new = await ask(client, headers, private_question)
            follow_up = await ask(client, headers, private_question, kept)

    for response in (new, follow_up):
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "unavailable"
        assert response.json()["error"]["message"] == "The chat is unavailable right now"
    assert len(failing.calls) == 2
    assert await counts(admin_engine) == (1, 2)  # only the earlier exchange

    logged = [record for record in caplog.records if record.getMessage() == "chat_unavailable"]
    assert [getattr(record, "error", None) for record in logged] == ["RuntimeError"] * 2
    everything = " ".join(str(vars(record)) for record in caplog.records)
    assert "secret plan" not in everything
    assert "throttled" not in everything


@pytest.mark.parametrize(
    "body",
    [
        {"question": "   "},
        {"question": "ab"},
        {"question": "x" * 1001},
        {"question": 123},
        {},
        {"question": QUESTION, "conversation_id": "not-a-uuid"},
    ],
    ids=["blank", "too-short", "too-long", "not-text", "missing", "bad-uuid"],
)
async def test_a_malformed_question_is_refused_before_the_engine(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    body: dict[str, Any],
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    engine = FakeChatEngine()
    async with chat_app(db_config, engine) as client:
        response = await client.post(MESSAGES, json=body, headers=headers)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert "input" not in str(error["details"])  # the caller's text is never echoed back
    assert engine.calls == []
    assert await counts(admin_engine) == (0, 0)


async def test_the_longest_question_is_accepted(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    async with chat_app(db_config, FakeChatEngine()) as client:
        response = await ask(client, headers, "x" * 1000)
    assert response.status_code == 200


async def test_a_malformed_conversation_id_in_the_path_is_refused(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    async with chat_app(db_config, FakeChatEngine()) as client:
        response = await client.get(f"{CONVERSATIONS}/not-a-uuid", headers=headers)
    assert response.status_code == 422


async def test_deleting_the_user_removes_their_conversations_and_messages(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    user_id, headers = await sign_in(make_user, session_factory)
    async with chat_app(db_config, FakeChatEngine()) as client:
        await ask(client, headers)
    assert await counts(admin_engine) == (1, 2)

    async with admin_engine.begin() as connection:
        await connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
    assert await counts(admin_engine) == (0, 0)


# --- the store ----------------------------------------------------------------------------------


def test_a_title_is_the_question_on_one_line_cut_to_80_characters() -> None:
    assert conversation_title("  What  was\tDemoCo's\nprofit? ") == "What was DemoCo's profit?"
    assert conversation_title("y" * 200) == "y" * 80


async def test_the_history_is_the_last_messages_oldest_first(
    make_user: MakeUser, session_factory: Factory
) -> None:
    user_id = await make_user()
    async with session_factory() as db:
        conversation = await create_conversation(db, user_id, "DemoCo")
        for n in range(4):
            await add_exchange(db, conversation, f"Question {n}", answered(f"Answer {n}"))
            await db.commit()  # one transaction per exchange, as the api does
        last_four = await history(db, conversation, limit=4)
        everything = await history(db, conversation)

    assert last_four == [
        Turn(role="user", text="Question 2"),
        Turn(role="assistant", text="Answer 2", sources=(DEMO_SOURCE,)),  # "where from?"
        Turn(role="user", text="Question 3"),
        Turn(role="assistant", text="Answer 3", sources=(DEMO_SOURCE,)),
    ]
    assert len(everything) == 8


async def test_add_exchange_returns_both_messages(
    make_user: MakeUser, session_factory: Factory
) -> None:
    user_id = await make_user()
    reply = Reply(
        text="DemoCo's debt-to-equity was 0.25 [1].",
        status="answered",
        sources=(DEMO_SOURCE,),
        model="fake-llm",
        input_tokens=7,
        output_tokens=3,
    )
    async with session_factory() as db:
        conversation = await create_conversation(db, user_id, "DemoCo")
        question, answer = await add_exchange(db, conversation, "Debt to equity?", reply)
        await db.commit()

    assert (question.role, question.text, question.status, question.sources) == (
        "user",
        "Debt to equity?",
        None,
        [],
    )
    assert (answer.role, answer.text, answer.status) == ("assistant", reply.text, "answered")
    assert answer.sources == [
        {
            "marker": 1,
            "source": "filing",
            "label": DEMO_SOURCE.label,
            "url": DEMO_SOURCE.url,
            "quote": DEMO_SOURCE.quote,
        }
    ]


async def test_ownership_and_reading_are_per_user(
    make_user: MakeUser, session_factory: Factory
) -> None:
    owner = await make_user()
    other = await make_user()
    async with session_factory() as db:
        conversation = await create_conversation(db, owner, "DemoCo")
        await db.commit()
        assert await owns_conversation(db, owner, conversation) is True
        assert await owns_conversation(db, other, conversation) is False
        assert await get_conversation(db, other, conversation) is None
        found = await get_conversation(db, owner, conversation)

    assert found is not None
    assert (found.id, found.title, found.messages) == (conversation, "DemoCo", [])


async def test_the_spend_counts_every_answer_of_every_user(
    make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    prices = (Decimal("0.35"), Decimal("2.95"))
    async with session_factory() as db:
        assert await chat_spent_usd(db, *prices) == Decimal("0")

    await spend(admin_engine, await make_user(), 1_000_000, 100_000)
    await spend(admin_engine, await make_user(), 2_000_000, 0)
    async with session_factory() as db:
        spent = await chat_spent_usd(db, *prices)

    assert spent == Decimal("0.35") * 3 + Decimal("2.95") / 10  # $1.345


async def test_the_choices_of_a_question_asked_back_come_with_the_live_reply_only(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    asked_back = replace(
        answered("Which company do you mean?"),
        sources=(),
        choices=(Choice("TCS", "What is the latest revenue for TCS?"),),
    )

    async with chat_app(db_config, FakeChatEngine([asked_back])) as client:
        posted = (await ask(client, headers)).json()
        listed = (
            await client.get(f"{CONVERSATIONS}/{posted['conversation_id']}", headers=headers)
        ).json()

    assert posted["answer"]["choices"] == [
        {"label": "TCS", "question": "What is the latest revenue for TCS?"}
    ]
    assert posted["question"]["choices"] == []
    assert [m["choices"] for m in listed["messages"]] == [[], []]  # never stored
