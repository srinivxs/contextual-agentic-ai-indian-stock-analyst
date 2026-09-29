"""Revision 0010: feed_items, feed_state, and events that may cite a feed item (P15).

Every rule of the feed's idempotency and of the event's "one source" is a constraint, so a bug in
app code cannot store a duplicate item or an event with two (or no) sources.
"""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.integration.conftest import Migrator

pytestmark = pytest.mark.usefixtures("migrated_db")

HASH_A = "a" * 64
HASH_B = "b" * 64

ITEM = (
    "INSERT INTO feed_items (source, canonical_url, title, title_hash, published_at, summary, "
    "is_fixture) VALUES (:source, :url, :title, :hash, :published, :summary, :fixture) "
    "RETURNING id"
)


def item(**overrides: Any) -> dict[str, Any]:
    return {
        "source": "rbi",
        "url": "https://example.test/one",
        "title": "DemoCo press release",
        "hash": HASH_A,
        "published": None,
        "summary": "",
        "fixture": True,
        **overrides,
    }


async def add_item(engine: AsyncEngine, **overrides: Any) -> int:
    async with engine.begin() as connection:
        found: int = (await connection.execute(text(ITEM), item(**overrides))).scalar_one()
        return found


async def count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as connection:
        found: int = (
            await connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
        ).scalar_one()
        return found


@pytest.fixture(autouse=True)
async def empty_feed(admin_engine: AsyncEngine) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(text("TRUNCATE feed_items, feed_state, events, documents CASCADE"))


async def test_a_well_formed_item_is_accepted_and_the_runtime_role_can_use_it(
    app_engine: AsyncEngine,
) -> None:
    async with app_engine.begin() as connection:
        await connection.execute(text(ITEM), item(summary="x" * 600, published=None))
        await connection.execute(
            text(
                "INSERT INTO feed_state (source, etag, last_result) VALUES ('rbi', 'e', 'ok') "
                "ON CONFLICT (source) DO UPDATE SET etag = 'f'"
            )
        )
    assert await count(app_engine, "feed_items") == 1
    assert await count(app_engine, "feed_state") == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"source": "sebi"},
        {"title": ""},
        {"title": "x" * 501},
        {"hash": "A" * 64},  # upper-case
        {"hash": "a" * 63},
        {"summary": "x" * 601},
        {"fixture": None},
    ],
    ids=["source", "empty-title", "long-title", "upper-hash", "short-hash", "long-summary", "flag"],
)
async def test_a_malformed_item_is_refused(
    admin_engine: AsyncEngine, overrides: dict[str, Any]
) -> None:
    with pytest.raises(DBAPIError):
        await add_item(admin_engine, **overrides)
    assert await count(admin_engine, "feed_items") == 0


async def test_the_same_url_or_the_same_title_hash_is_one_item(admin_engine: AsyncEngine) -> None:
    await add_item(admin_engine)
    with pytest.raises(IntegrityError):
        await add_item(admin_engine, hash=HASH_B)  # same URL
    with pytest.raises(IntegrityError):
        await add_item(admin_engine, url="https://example.test/two")  # same title hash
    await add_item(admin_engine, url="https://example.test/two", hash=HASH_B)
    assert await count(admin_engine, "feed_items") == 2


@pytest.mark.parametrize(
    "values",
    ["('rbi', 'exploded')", "('other', NULL)"],
    ids=["unknown-result", "unknown-source"],
)
async def test_a_malformed_state_is_refused(admin_engine: AsyncEngine, values: str) -> None:
    with pytest.raises(DBAPIError):
        async with admin_engine.begin() as connection:
            await connection.execute(
                text(f"INSERT INTO feed_state (source, last_result) VALUES {values}")  # noqa: S608
            )
    assert await count(admin_engine, "feed_state") == 0


# --- events ---------------------------------------------------------------------------------------

EVENT = (
    "INSERT INTO events (stock_id, document_id, page_number, feed_item_id, event_type, sentiment, "
    "impact, event_date, date_source, summary, quote) "
    "SELECT id, :document, :page, :feed_item, 'other', 'neutral', 'low', DATE '2026-09-01', "
    "'document', 'DemoCo item', 'DemoCo item' FROM stocks WHERE symbol = 'TCS'"
)


async def add_event(engine: AsyncEngine, **values: Any) -> None:
    params = {"document": None, "page": None, "feed_item": None, **values}
    async with engine.begin() as connection:
        await connection.execute(text(EVENT), params)


async def add_document(engine: AsyncEngine) -> int:
    async with engine.begin() as connection:
        found: int = (
            await connection.execute(
                text(
                    "INSERT INTO documents (stock_id, title, sha256, size_bytes, blob_key, source, "
                    "source_url, status) SELECT id, 'DemoCo', repeat('c', 64), 1, "
                    "'documents/' || repeat('c', 64) || '.pdf', 'bse', "
                    "'https://www.bseindia.com/x.pdf', 'completed' FROM stocks "
                    "WHERE symbol = 'TCS' RETURNING id"
                )
            )
        ).scalar_one()
        return found


async def test_an_event_may_cite_a_feed_item_alone(admin_engine: AsyncEngine) -> None:
    await add_event(admin_engine, feed_item=await add_item(admin_engine))
    assert await count(admin_engine, "events") == 1


async def test_an_event_may_cite_a_filing_page_alone(admin_engine: AsyncEngine) -> None:
    await add_event(admin_engine, document=await add_document(admin_engine), page=3)
    assert await count(admin_engine, "events") == 1


async def test_an_event_needs_exactly_one_source(admin_engine: AsyncEngine) -> None:
    feed_item = await add_item(admin_engine)
    document = await add_document(admin_engine)
    with pytest.raises(IntegrityError):
        await add_event(admin_engine)  # none
    with pytest.raises(IntegrityError):
        await add_event(admin_engine, document=document, page=1, feed_item=feed_item)  # both
    assert await count(admin_engine, "events") == 0


async def test_a_filing_event_still_needs_its_page(admin_engine: AsyncEngine) -> None:
    with pytest.raises(IntegrityError):
        await add_event(admin_engine, document=await add_document(admin_engine))


async def test_one_event_per_feed_item_and_stock(admin_engine: AsyncEngine) -> None:
    feed_item = await add_item(admin_engine)
    await add_event(admin_engine, feed_item=feed_item)
    with pytest.raises(IntegrityError):
        await add_event(admin_engine, feed_item=feed_item)


async def test_deleting_a_feed_item_removes_its_events(admin_engine: AsyncEngine) -> None:
    feed_item = await add_item(admin_engine)
    await add_event(admin_engine, feed_item=feed_item)
    async with admin_engine.begin() as connection:
        await connection.execute(text("DELETE FROM feed_items WHERE id = :id"), {"id": feed_item})
    assert await count(admin_engine, "events") == 0


async def test_downgrading_removes_feed_events_and_restores_the_not_nulls(
    migrator: Migrator, admin_engine: AsyncEngine
) -> None:
    document = await add_document(admin_engine)
    await add_event(admin_engine, feed_item=await add_item(admin_engine))
    await add_event(admin_engine, document=document, page=2)

    await migrator.downgrade("0009")
    try:
        async with admin_engine.connect() as connection:
            remaining = (await connection.execute(text("SELECT count(*) FROM events"))).scalar_one()
            tables = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                        "AND tablename IN ('feed_items', 'feed_state')"
                    )
                )
            ).scalar_one()
        assert (remaining, tables) == (1, 0)
        with pytest.raises(IntegrityError):
            async with admin_engine.begin() as connection:
                await connection.execute(
                    text(
                        "INSERT INTO events (stock_id, document_id, page_number, event_type, "
                        "sentiment, impact, event_date, date_source, summary, quote) "
                        "VALUES (1, NULL, NULL, 'other', 'neutral', 'low', DATE '2026-09-01', "
                        "'document', 's', 'q')"
                    )
                )
    finally:
        await migrator.upgrade("head")
