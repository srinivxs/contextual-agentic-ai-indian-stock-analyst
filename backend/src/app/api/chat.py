"""The grounded chat over HTTP (P12). Signed-in only.

    POST /api/v1/chat/messages                   ask a question (in a new or an existing
                                                 conversation); the answer comes back and both
                                                 are stored
    GET  /api/v1/chat/conversations              your conversations, most recent first (20)
    GET  /api/v1/chat/conversations/{id}         one of them, with its messages oldest first

Asking, the checks run in this order, each before anything costs money: Origin, session, the body
(422), the switch (409 without an engine), the spending cap (503 once the stored tokens reach
CHAT_BUDGET_USD), the conversation is yours (404, the same for someone else's and for one that
does not exist).

No transaction is open while the engine works (the project notes: no transaction spans a network call):

    (a) short read: the spend so far, and the conversation's recent history
    (b) the engine answers: retrieval and the LLM, seconds, no connection held
    (c) short write: the conversation (if new), the question and the answer, together

So a failing engine stores nothing at all, not even an empty conversation. The question and the
answer are the user's own text: they are never logged.
"""

import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.auth.deps import current_user, require_same_origin
from app.auth.sessions import CurrentUser
from app.chat.contract import ReplyStatus, Turn
from app.chat.store import (
    ConversationView,
    MessageView,
    add_exchange,
    chat_spent_usd,
    conversation_title,
    create_conversation,
    get_conversation,
    history,
    list_conversations,
    owns_conversation,
)
from app.core.errors import AppError

logger = logging.getLogger("app.chat")

router = APIRouter(prefix="/api/v1", tags=["chat"])


class AskIn(BaseModel):
    # At least three characters and at least one that is not a space.
    question: Annotated[str, Field(min_length=3, max_length=1000, pattern=r"\S")]
    conversation_id: UUID | None = None  # None: start a new conversation


class SourceOut(BaseModel):
    marker: int
    source: Literal["filing", "screener", "rbi", "derived"]
    label: str
    url: str | None
    quote: str | None


class MessageOut(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    text: str
    status: ReplyStatus | None
    sources: list[SourceOut]
    created_at: str


class ExchangeOut(BaseModel):
    conversation_id: UUID
    question: MessageOut
    answer: MessageOut


class ConversationSummaryOut(BaseModel):
    id: UUID
    title: str
    created_at: str
    updated_at: str


class ConversationsOut(BaseModel):
    items: list[ConversationSummaryOut]


class ConversationOut(ConversationSummaryOut):
    messages: list[MessageOut]


def _message_out(view: MessageView) -> MessageOut:
    return MessageOut(
        id=view.id,
        role=view.role,
        text=view.text,
        status=view.status,
        sources=[SourceOut(**source) for source in view.sources],
        created_at=view.created_at.isoformat(),
    )


def _summary_out(view: ConversationView) -> ConversationSummaryOut:
    return ConversationSummaryOut(
        id=view.id,
        title=view.title,
        created_at=view.created_at.isoformat(),
        updated_at=view.updated_at.isoformat(),
    )


def _no_such_conversation() -> AppError:
    return AppError(status_code=404, code="not_found", message="No such conversation")


@router.post(
    "/chat/messages",
    summary="Ask a question; get a cited answer, stored in the conversation",
    dependencies=[Depends(require_same_origin)],
)
async def ask(
    body: AskIn, request: Request, user: Annotated[CurrentUser, Depends(current_user)]
) -> ExchangeOut:
    engine = request.app.state.chat_engine
    if engine is None:
        raise AppError(status_code=409, code="conflict", message="Chat is switched off")
    settings = request.app.state.settings
    factory = request.app.state.session_factory
    question = body.question.strip()

    # (a) Read only: nothing is written before the engine has answered.
    async with factory() as db:
        spent = await chat_spent_usd(
            db, settings.llm_input_usd_per_mtok, settings.llm_output_usd_per_mtok
        )
        if spent >= settings.chat_budget_usd:
            raise AppError(
                status_code=503, code="unavailable", message="The chat's spending cap is used up"
            )
        if body.conversation_id is not None and not await owns_conversation(
            db, user.id, body.conversation_id
        ):
            raise _no_such_conversation()
        # A new conversation has no history yet.
        past: list[Turn] = (
            [] if body.conversation_id is None else await history(db, body.conversation_id)
        )

    # (b) The engine, with no transaction open.
    try:
        reply = await engine.answer(question=question, history=past, user_id=user.id)
    except Exception as error:
        # By type only: the message may carry AWS details or the prompt, and the question is the
        # user's own text (never logged).
        logger.warning("chat_unavailable", extra={"error": type(error).__name__})
        raise AppError(
            status_code=503, code="unavailable", message="The chat is unavailable right now"
        ) from error

    # (c) One short transaction: a question is never stored without its answer.
    async with factory() as db:
        conversation_id = body.conversation_id or await create_conversation(
            db, user.id, conversation_title(question)
        )
        asked, answer = await add_exchange(db, conversation_id, question, reply)
        await db.commit()
    return ExchangeOut(
        conversation_id=conversation_id,
        question=_message_out(asked),
        answer=_message_out(answer),
    )


@router.get("/chat/conversations", summary="Your conversations, most recent first")
async def conversations(
    request: Request, user: Annotated[CurrentUser, Depends(current_user)]
) -> ConversationsOut:
    async with request.app.state.session_factory() as db:
        views = await list_conversations(db, user.id)
    return ConversationsOut(items=[_summary_out(view) for view in views])


@router.get("/chat/conversations/{conversation_id}", summary="One conversation, oldest first")
async def conversation(
    conversation_id: UUID, request: Request, user: Annotated[CurrentUser, Depends(current_user)]
) -> ConversationOut:
    async with request.app.state.session_factory() as db:
        view = await get_conversation(db, user.id, conversation_id)
    if view is None:
        raise _no_such_conversation()
    return ConversationOut(
        **_summary_out(view).model_dump(),
        messages=[_message_out(message) for message in view.messages],
    )
