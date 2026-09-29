"""Storing the RBI feed (P15): plain SQL, one short transaction owned by the caller.

Nothing here commits, and nothing here talks to the network. Idempotency is the database's job:
``feed_items`` is UNIQUE on (source, canonical_url) and on (source, title_hash), so
``ON CONFLICT DO NOTHING`` (no target, so it covers both) stores an item once however many polls,
workers or retries see it, and ``events`` is UNIQUE on (feed_item_id, stock_id).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.feeds.model import FEED_SOURCE, FeedItem
from app.feeds.tagging import ALIASES, stocks_named, tag

SUMMARY_LIMIT = 200  # events.summary is CHECKed to 1..200
QUOTE_LIMIT = 400  # events.quote is CHECKed to 1..400

_INSERT_ITEM = text(
    """
    INSERT INTO feed_items (source, canonical_url, title, title_hash, published_at, summary,
                            is_fixture)
    VALUES (:source, :url, :title, :hash, :published, :summary, :fixture)
    ON CONFLICT DO NOTHING
    RETURNING id
    """
)

_RECENT = text(
    """
    SELECT canonical_url, title, title_hash, published_at, summary, is_fixture
    FROM feed_items
    WHERE source = :source
    ORDER BY published_at DESC NULLS LAST, id DESC
    LIMIT :limit
    """
)

_INSERT_EVENT = text(
    """
    INSERT INTO events (stock_id, feed_item_id, event_type, sentiment, impact, event_date,
                        date_source, summary, quote)
    SELECT id, :item, :event_type, :sentiment, :impact, :day, :date_source, :summary, :quote
    FROM stocks WHERE symbol = :symbol
    ON CONFLICT (feed_item_id, stock_id) DO NOTHING
    """
)

_GET_STATE = text(
    "SELECT etag, last_modified, last_polled_at, last_result FROM feed_state WHERE source = :source"
)

_SAVE_STATE = text(
    """
    INSERT INTO feed_state (source, etag, last_modified, last_polled_at, last_result)
    VALUES (:source, :etag, :last_modified, now(), :last_result)
    ON CONFLICT (source) DO UPDATE SET
        etag = EXCLUDED.etag,
        last_modified = EXCLUDED.last_modified,
        last_polled_at = EXCLUDED.last_polled_at,
        last_result = EXCLUDED.last_result
    """
)


@dataclass(frozen=True)
class FeedState:
    """What the next conditional GET sends back, and how the last poll ended."""

    etag: str | None
    last_modified: str | None
    last_polled_at: datetime | None
    last_result: str | None


async def add_items(db: AsyncSession, items: list[FeedItem]) -> list[tuple[int, FeedItem]]:
    """Store the items not seen before; return (id, item) for exactly those."""
    stored: list[tuple[int, FeedItem]] = []
    for item in items:
        new_id = (
            await db.execute(
                _INSERT_ITEM,
                {
                    "source": FEED_SOURCE,
                    "url": item.canonical_url,
                    "title": item.title,
                    "hash": item.title_hash,
                    "published": item.published_at,
                    "summary": item.summary,
                    "fixture": item.is_fixture,
                },
            )
        ).scalar_one_or_none()
        if new_id is not None:
            stored.append((new_id, item))
    return stored


async def recent_items(db: AsyncSession, limit: int = 10) -> list[FeedItem]:
    """The newest items first; undated ones last."""
    rows = await db.execute(_RECENT, {"source": FEED_SOURCE, "limit": limit})
    return [FeedItem(**row._mapping) for row in rows]


async def get_state(db: AsyncSession) -> FeedState | None:
    row = (await db.execute(_GET_STATE, {"source": FEED_SOURCE})).one_or_none()
    return None if row is None else FeedState(**row._mapping)


async def save_state(
    db: AsyncSession, *, etag: str | None, last_modified: str | None, last_result: str
) -> None:
    """Record how a poll ended (and stamp the time). The caller passes the validators to keep."""
    await db.execute(
        _SAVE_STATE,
        {
            "source": FEED_SOURCE,
            "etag": etag,
            "last_modified": last_modified,
            "last_result": last_result,
        },
    )


def _cut(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


async def add_feed_events(
    db: AsyncSession,
    stored: list[tuple[int, FeedItem]],
    aliases: Mapping[str, tuple[str, ...]] = ALIASES,
    *,
    today: date | None = None,
) -> int:
    """One event per (newly stored item, followed stock its title names). Returns how many.

    Dated by the item's own publication date when it has one, else the day it was fetched. The
    event's quote is the title: the one sentence the source itself wrote.
    """
    count = 0
    for item_id, item in stored:
        symbols = stocks_named(item.title, aliases)
        if not symbols:
            continue
        event_type, sentiment, impact = tag(item.title)
        if item.published_at is not None:
            day, date_source = item.published_at.astimezone(UTC).date(), "document"
        else:
            day, date_source = today or datetime.now(UTC).date(), "fetched"
        for symbol in symbols:
            result = cast(
                "CursorResult[Any]",
                await db.execute(
                    _INSERT_EVENT,
                    {
                        "item": item_id,
                        "symbol": symbol,
                        "event_type": event_type,
                        "sentiment": sentiment,
                        "impact": impact,
                        "day": day,
                        "date_source": date_source,
                        "summary": _cut(item.title, SUMMARY_LIMIT),
                        "quote": _cut(item.title, QUOTE_LIMIT),
                    },
                ),
            )
            count += result.rowcount
    return count
