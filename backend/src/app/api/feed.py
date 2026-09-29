"""The newest RBI press releases the worker has stored (P15, ADR 007).

    GET /api/v1/feed

Signed in only. Shows the title, the date, about 300 characters of the summary and a link to the
release on rbi.org.in, the same display policy as the filings. The synthetic fixture items (the
offline FEED_MODE=fixture) carry no link and say so with ``is_fixture``. Nothing here calls the
network: the worker fetched and stored everything already.
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.auth.deps import current_user
from app.auth.sessions import CurrentUser
from app.feeds.store import recent_items
from app.retrieval import excerpt

router = APIRouter(prefix="/api/v1", tags=["feed"])

ATTRIBUTION = "Source: Reserve Bank of India press releases (rbi.org.in)"
FEED_LIMIT = 10


class FeedItemOut(BaseModel):
    title: str
    published_at: datetime | None
    summary: str
    url: str | None
    is_fixture: bool


class FeedOut(BaseModel):
    items: list[FeedItemOut]
    attribution: str


@router.get("/feed", summary="The newest RBI press releases")
async def feed(request: Request, user: Annotated[CurrentUser, Depends(current_user)]) -> FeedOut:
    async with request.app.state.session_factory() as db:
        items = await recent_items(db, FEED_LIMIT)
    return FeedOut(
        items=[
            FeedItemOut(
                title=item.title,
                published_at=item.published_at,
                summary=excerpt(item.summary),
                url=None if item.is_fixture else item.canonical_url,
                is_fixture=item.is_fixture,
            )
            for item in items
        ],
        attribution=ATTRIBUTION,
    )
