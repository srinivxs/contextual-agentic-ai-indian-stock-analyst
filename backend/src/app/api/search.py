"""Search the filings by meaning (P10b, ADR 019).

    GET /api/v1/search?q=<question>[&symbol=TCS]

Checks, each before anything costs money: session, then the question and symbol (422), then the
switch (409 when EMBEDDINGS_ENABLED is off), then the stock exists (404). Only then is the question
turned into a fingerprint: one Bedrock call per search, a few tokens. If Bedrock fails (throttled,
an expired pass) the answer is a plain 503; the AWS error text never reaches the browser.

Each result is a short excerpt (about 300 characters), its filing and page, and the official BSE
link: never the stored file or the whole passage.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel

from app.auth.deps import current_user
from app.auth.sessions import CurrentUser
from app.clock import india_today
from app.core.errors import AppError
from app.documents import stock_id
from app.retrieval import excerpt, search

logger = logging.getLogger("app.search")

router = APIRouter(prefix="/api/v1", tags=["search"])

# At least three characters and at least one that is not a space.
Question = Annotated[str, Query(min_length=3, max_length=300, pattern=r"\S")]
OptionalSymbol = Annotated[str | None, Query(pattern=r"^[A-Z0-9&-]{1,20}$")]


class SearchHit(BaseModel):
    symbol: str
    document_id: int
    title: str
    kind: str | None
    period: str | None
    page: int
    excerpt: str
    source_url: str | None
    score: float


class SearchResponse(BaseModel):
    items: list[SearchHit]


async def _is_a_stock(request: Request, symbol: str) -> bool:
    async with request.app.state.session_factory() as db:
        return await stock_id(db, symbol) is not None


@router.get("/search", summary="The passages of the filings closest in meaning to a question")
async def search_filings(
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
    q: Question,
    symbol: OptionalSymbol = None,
) -> SearchResponse:
    embedder = request.app.state.embedder
    if embedder is None:
        raise AppError(status_code=409, code="conflict", message="Search is switched off")
    if symbol is not None and not await _is_a_stock(request, symbol):
        # Deliberately does not repeat the symbol the caller sent.
        raise AppError(status_code=404, code="not_found", message="No such stock")
    try:
        results = await search(
            request.app.state.session_factory,
            embedder,
            q.strip(),
            symbol=symbol,
            today=india_today(),
        )
    except Exception as error:
        # Logged by type only: the message may carry AWS details, and the question is the
        # user's own text (never logged, like prompts).
        logger.warning("search_unavailable", extra={"error": type(error).__name__})
        raise AppError(
            status_code=503, code="unavailable", message="Search is unavailable right now"
        ) from error
    return SearchResponse(
        items=[
            SearchHit(
                symbol=r.passage.symbol,
                document_id=r.passage.document_id,
                title=r.passage.title,
                kind=r.passage.kind,
                period=r.passage.period,
                page=r.passage.page,
                excerpt=excerpt(r.passage.text),
                source_url=r.passage.source_url,
                score=round(r.score, 3),
            )
            for r in results
        ]
    )
