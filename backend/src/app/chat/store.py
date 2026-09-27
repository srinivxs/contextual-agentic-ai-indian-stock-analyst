"""The chat's history in the database: conversations and their messages (P12, migration 0008).

Plain SQL, like the rest of the data access (ADR 013). None of these functions commits; the caller
owns the transaction. Every read that could reach a conversation takes the user id from the
session, so a request can never read someone else's conversation.

Within one exchange the question and the reply share a timestamp (one transaction, one ``now()``),
so every ordering adds a tie-break: the question comes before its reply.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.contract import Reply, ReplyStatus, Turn

TITLE_LENGTH = 80  # the database's limit too (ck_conversations_title_length)
_PER_MILLION = Decimal(1_000_000)

_CREATE = text("INSERT INTO conversations (user_id, title) VALUES (:user_id, :title) RETURNING id")

_OWNS = text("SELECT EXISTS (SELECT 1 FROM conversations WHERE id = :id AND user_id = :user_id)")

_CONVERSATION = text(
    "SELECT id, title, created_at, updated_at FROM conversations "
    "WHERE id = :id AND user_id = :user_id"
)

_LIST = text(
    "SELECT id, title, created_at, updated_at FROM conversations WHERE user_id = :user_id "
    "ORDER BY updated_at DESC, id LIMIT :limit"
)

# ``sources::text``: parsed here, so the result does not depend on the driver's jsonb handling.
_MESSAGES = text(
    "SELECT id, role, text, status, sources::text AS sources, created_at FROM messages "
    "WHERE conversation_id = :id ORDER BY created_at, role = 'assistant'"
)

# The last ``limit`` messages, then turned back to oldest first.
_HISTORY = text(
    "SELECT role, text FROM ("
    "  SELECT role, text, created_at, role = 'assistant' AS is_reply FROM messages"
    "  WHERE conversation_id = :id ORDER BY created_at DESC, is_reply DESC LIMIT :limit"
    ") AS last ORDER BY created_at, is_reply"
)

_ADD_MESSAGE = text(
    "INSERT INTO messages (conversation_id, role, text, status, sources, model, input_tokens, "
    "output_tokens) VALUES (:conversation_id, :role, :text, :status, CAST(:sources AS jsonb), "
    ":model, :input_tokens, :output_tokens) RETURNING id, created_at"
)

_TOUCH = text("UPDATE conversations SET updated_at = now() WHERE id = :id")

_TOKENS = text(
    "SELECT COALESCE(sum(input_tokens), 0) AS input_tokens, "
    "COALESCE(sum(output_tokens), 0) AS output_tokens FROM messages WHERE role = 'assistant'"
)


@dataclass(frozen=True)
class MessageView:
    id: UUID
    role: Literal["user", "assistant"]
    text: str
    status: ReplyStatus | None  # None for the user's question
    sources: list[dict[str, Any]]  # {marker, source, label, url, quote}; [] for a question
    created_at: datetime


@dataclass(frozen=True)
class ConversationView:
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[MessageView] = field(default_factory=list)  # empty in the list of conversations


def conversation_title(question: str) -> str:
    """The question on one line (runs of spaces, tabs and newlines become one space), cut to 80."""
    return " ".join(question.split())[:TITLE_LENGTH]


async def create_conversation(db: AsyncSession, user_id: UUID, title: str) -> UUID:
    found: UUID = (await db.execute(_CREATE, {"user_id": user_id, "title": title})).scalar_one()
    return found


async def owns_conversation(db: AsyncSession, user_id: UUID, conversation_id: UUID) -> bool:
    owns: bool = (await db.execute(_OWNS, {"id": conversation_id, "user_id": user_id})).scalar_one()
    return owns


def _message(row: Row[Any]) -> MessageView:
    return MessageView(
        id=row.id,
        role=row.role,
        text=row.text,
        status=row.status,
        sources=json.loads(row.sources),
        created_at=row.created_at,
    )


async def get_conversation(
    db: AsyncSession, user_id: UUID, conversation_id: UUID
) -> ConversationView | None:
    """The conversation with every message, oldest first. None when it does not exist OR belongs
    to someone else: the api answers both with the same 404."""
    row = (
        await db.execute(_CONVERSATION, {"id": conversation_id, "user_id": user_id})
    ).one_or_none()
    if row is None:
        return None
    messages = await db.execute(_MESSAGES, {"id": conversation_id})
    return ConversationView(
        id=row.id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        messages=[_message(message) for message in messages],
    )


async def list_conversations(
    db: AsyncSession, user_id: UUID, limit: int = 20
) -> list[ConversationView]:
    """The user's conversations, the most recently used first, without their messages."""
    result = await db.execute(_LIST, {"user_id": user_id, "limit": limit})
    return [
        ConversationView(
            id=row.id, title=row.title, created_at=row.created_at, updated_at=row.updated_at
        )
        for row in result
    ]


async def history(db: AsyncSession, conversation_id: UUID, limit: int = 10) -> list[Turn]:
    """The last ``limit`` messages, oldest first: what a follow-up question is read against.

    Takes no user id: the caller has already checked the conversation is the user's own."""
    result = await db.execute(_HISTORY, {"id": conversation_id, "limit": limit})
    return [Turn(role=row.role, text=row.text) for row in result]


async def _add_message(
    db: AsyncSession, conversation_id: UUID, values: dict[str, Any]
) -> MessageView:
    row = (
        await db.execute(
            _ADD_MESSAGE,
            {
                **values,
                "conversation_id": conversation_id,
                "sources": json.dumps(values["sources"]),
            },
        )
    ).one()
    return MessageView(
        id=row.id,
        role=values["role"],
        text=values["text"],
        status=values["status"],
        sources=values["sources"],
        created_at=row.created_at,
    )


async def add_exchange(
    db: AsyncSession, conversation_id: UUID, question: str, reply: Reply
) -> tuple[MessageView, MessageView]:
    """Store the question and its reply, and mark the conversation as just used. Both messages
    go in together, in the caller's transaction: a question is never kept without its reply."""
    asked = await _add_message(
        db,
        conversation_id,
        {
            "role": "user",
            "text": question,
            "status": None,
            "sources": [],
            "model": None,
            "input_tokens": 0,
            "output_tokens": 0,
        },
    )
    answer = await _add_message(
        db,
        conversation_id,
        {
            "role": "assistant",
            "text": reply.text,
            "status": reply.status,
            "sources": [asdict(source) for source in reply.sources],
            "model": reply.model,
            "input_tokens": reply.input_tokens,
            "output_tokens": reply.output_tokens,
        },
    )
    await db.execute(_TOUCH, {"id": conversation_id})
    return asked, answer


async def chat_spent_usd(
    db: AsyncSession, input_usd_per_mtok: Decimal, output_usd_per_mtok: Decimal
) -> Decimal:
    """What the chat has cost so far, every user together: the tokens stored with every reply,
    priced per million. Exact (Decimal), so the cap is never missed by a rounding error."""
    row = (await db.execute(_TOKENS)).one()
    return (
        Decimal(row.input_tokens) * input_usd_per_mtok
        + Decimal(row.output_tokens) * output_usd_per_mtok
    ) / _PER_MILLION
