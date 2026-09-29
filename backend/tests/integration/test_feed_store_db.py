"""The feed's storage against the real database (P15): idempotency comes from the constraints."""

import asyncio
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.feeds.model import FeedItem
from app.feeds.rbi import title_hash
from app.feeds.store import (
    add_feed_events,
    add_items,
    get_state,
    recent_items,
    save_state,
)

pytestmark = pytest.mark.usefixtures("migrated_db")

Factory = async_sessionmaker[AsyncSession]
DEMO_ALIASES = {"TCS": ("DemoCo",), "RELIANCE": ("Acme Corp",)}  # synthetic names on real symbols


@pytest.fixture(autouse=True)
async def empty_feed(admin_engine: AsyncEngine) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(text("TRUNCATE feed_items, feed_state, events CASCADE"))


def make_item(
    n: int = 1,
    *,
    title: str | None = None,
    url: str | None = None,
    published: datetime | None = datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
    summary: str = "Synthetic summary.",
    fixture: bool = True,
) -> FeedItem:
    title = title or f"Synthetic release number {n}"
    return FeedItem(
        canonical_url=url or f"https://www.rbi.org.in/demo/{n}",
        title=title,
        title_hash=title_hash(title),
        published_at=published,
        summary=summary,
        is_fixture=fixture,
    )


async def store(factory: Factory, items: list[FeedItem]) -> list[tuple[int, FeedItem]]:
    async with factory() as db:
        stored = await add_items(db, items)
        await db.commit()
    return stored


async def count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as connection:
        found: int = (
            await connection.execute(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
        ).scalar_one()
        return found


async def test_new_items_are_stored_and_returned_with_their_ids(session_factory: Factory) -> None:
    stored = await store(session_factory, [make_item(1), make_item(2)])
    assert [item.title for _, item in stored] == [
        "Synthetic release number 1",
        "Synthetic release number 2",
    ]
    assert len({item_id for item_id, _ in stored}) == 2


async def test_the_same_url_twice_is_one_item(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await store(session_factory, [make_item(1)])
    again = await store(session_factory, [make_item(1, title="A retitled release")])
    assert again == []
    assert await count(admin_engine, "feed_items") == 1


async def test_the_same_title_under_another_url_is_one_item(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await store(session_factory, [make_item(1)])
    again = await store(session_factory, [make_item(2, title="Synthetic release number 1")])
    assert again == []
    assert await count(admin_engine, "feed_items") == 1


async def test_a_batch_with_a_duplicate_inside_it_stores_it_once(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    stored = await store(session_factory, [make_item(1), make_item(1), make_item(2)])
    assert len(stored) == 2
    assert await count(admin_engine, "feed_items") == 2


async def test_two_polls_at_once_store_one_row_each(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    items = [make_item(n) for n in range(1, 6)]
    results = await asyncio.gather(*(store(session_factory, items) for _ in range(4)))
    assert await count(admin_engine, "feed_items") == 5
    # Between them the racers report each item as new exactly once.
    assert sum(len(found) for found in results) == 5


async def test_recent_items_come_newest_first_undated_last_and_limited(
    session_factory: Factory,
) -> None:
    await store(
        session_factory,
        [
            make_item(1, published=datetime(2026, 1, 1, tzinfo=UTC)),
            make_item(2, published=None),
            make_item(3, published=datetime(2026, 3, 1, tzinfo=UTC)),
            make_item(4, published=datetime(2026, 2, 1, tzinfo=UTC)),
        ],
    )
    async with session_factory() as db:
        everything = await recent_items(db)
        two = await recent_items(db, limit=2)
    assert [item.title[-1] for item in everything] == ["3", "4", "1", "2"]
    assert [item.title[-1] for item in two] == ["3", "4"]
    assert everything[0] == make_item(3, published=datetime(2026, 3, 1, tzinfo=UTC))


async def test_recent_items_of_an_empty_feed_is_empty(session_factory: Factory) -> None:
    async with session_factory() as db:
        assert await recent_items(db) == []


async def test_state_is_created_then_updated(session_factory: Factory) -> None:
    async with session_factory() as db:
        assert await get_state(db) is None
        await save_state(
            db, etag='"v1"', last_modified="Mon, 01 Sep 2026 10:00:00 GMT", last_result="ok"
        )
        await db.commit()
    async with session_factory() as db:
        first = await get_state(db)
        assert first is not None
        assert (first.etag, first.last_modified, first.last_result) == (
            '"v1"',
            "Mon, 01 Sep 2026 10:00:00 GMT",
            "ok",
        )
        assert first.last_polled_at is not None
        await save_state(
            db, etag='"v1"', last_modified=first.last_modified, last_result="not_modified"
        )
        await db.commit()
    async with session_factory() as db:
        second = await get_state(db)
    assert second is not None
    assert (second.etag, second.last_result) == ('"v1"', "not_modified")
    assert second.last_polled_at is not None
    assert first.last_polled_at is not None
    assert second.last_polled_at >= first.last_polled_at


async def events(engine: AsyncEngine) -> list[tuple[object, ...]]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT s.symbol, e.event_type, e.sentiment, e.impact, e.event_date, "
                "e.date_source, e.summary, e.quote, e.document_id, e.model, e.version "
                "FROM events e JOIN stocks s ON s.id = e.stock_id ORDER BY s.symbol, e.id"
            )
        )
        return [tuple(row) for row in rows]


async def test_only_an_item_naming_a_stock_becomes_an_event(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    stored = await store(
        session_factory,
        [
            make_item(1, title="Monetary penalty on DemoCo"),
            make_item(2, title="Weekly statistical supplement"),
        ],
    )
    async with session_factory() as db:
        added = await add_feed_events(db, stored, DEMO_ALIASES)
        await db.commit()

    assert added == 1
    assert await events(admin_engine) == [
        (
            "TCS",
            "regulatory_legal",
            "negative",
            "medium",
            date(2026, 9, 1),
            "document",
            "Monetary penalty on DemoCo",
            "Monetary penalty on DemoCo",
            None,
            None,
            None,
        )
    ]


async def test_an_item_naming_two_stocks_is_an_event_for_each(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    stored = await store(session_factory, [make_item(1, title="DemoCo and Acme Corp appointment")])
    async with session_factory() as db:
        assert await add_feed_events(db, stored, DEMO_ALIASES) == 2
        await db.commit()
    assert [row[0] for row in await events(admin_engine)] == ["RELIANCE", "TCS"]


async def test_an_undated_item_is_dated_the_day_it_was_fetched(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    stored = await store(session_factory, [make_item(1, title="DemoCo notice", published=None)])
    async with session_factory() as db:
        await add_feed_events(db, stored, DEMO_ALIASES, today=date(2026, 9, 29))
        await db.commit()
    [(_, _, _, _, day, date_source, *_)] = await events(admin_engine)
    assert (day, date_source) == (date(2026, 9, 29), "fetched")


async def test_a_very_long_title_is_cut_to_fit_the_event(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    title = "DemoCo " + "long " * 95  # about 480 characters
    stored = await store(session_factory, [make_item(1, title=title)])
    async with session_factory() as db:
        await add_feed_events(db, stored, DEMO_ALIASES)
        await db.commit()
    [row] = await events(admin_engine)
    summary, quote = str(row[6]), str(row[7])
    assert len(summary) <= 200
    assert summary.endswith("…")
    assert len(quote) <= 400


async def test_adding_the_events_again_creates_none(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    stored = await store(session_factory, [make_item(1, title="DemoCo notice")])
    for expected in (1, 0):
        async with session_factory() as db:
            assert await add_feed_events(db, stored, DEMO_ALIASES) == expected
            await db.commit()
    assert await count(admin_engine, "events") == 1


async def test_the_real_alias_table_is_the_default(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """Nothing in the synthetic fixtures names a real company, so the default table adds nothing."""
    stored = await store(session_factory, [make_item(1, title="Sample: a weekly supplement")])
    async with session_factory() as db:
        assert await add_feed_events(db, stored) == 0
    assert await count(admin_engine, "events") == 0
