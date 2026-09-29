"""GET /api/v1/feed, and RBI items as cited events in a stock's insights (P15), over HTTP against
the real database. Every company and release here is synthetic."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.feeds.model import FeedItem
from app.feeds.rbi import title_hash
from app.feeds.store import add_feed_events, add_items
from app.insights_store import load_stock
from tests.helpers import running_app
from tests.integration.auth_helpers import open_session
from tests.integration.conftest import DbConfig, MakeUser

pytestmark = pytest.mark.usefixtures("migrated_db", "clean_document_tables")

Factory = async_sessionmaker[AsyncSession]
ALIASES = {"TCS": ("DemoCo",)}  # a synthetic name pointed at a followed symbol


@pytest.fixture(autouse=True)
async def empty_feed(admin_engine: AsyncEngine) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(text("TRUNCATE feed_items, feed_state CASCADE"))


async def sign_in(make_user: MakeUser, session_factory: Factory) -> tuple[UUID, dict[str, str]]:
    user_id = await make_user()
    token = await open_session(session_factory, user_id)
    return user_id, {"Cookie": f"session={token}"}


def item(
    n: int,
    title: str,
    *,
    published: datetime | None = datetime(2026, 9, 1, 10, tzinfo=UTC),
    summary: str = "Synthetic text.",
    fixture: bool = False,
) -> FeedItem:
    return FeedItem(
        canonical_url=f"https://www.rbi.org.in/demo/{n}",
        title=title,
        title_hash=title_hash(title),
        published_at=published,
        summary=summary,
        is_fixture=fixture,
    )


async def seed(factory: Factory, items: list[FeedItem]) -> None:
    async with factory() as db:
        stored = await add_items(db, items)
        await add_feed_events(db, stored, ALIASES)
        await db.commit()


async def test_the_feed_needs_a_session(db_config: DbConfig) -> None:
    async with running_app(db_config.settings()) as (_, client):
        response = await client.get("/api/v1/feed")
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"


async def test_an_empty_feed_still_carries_its_attribution(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        response = await client.get("/api/v1/feed", headers=cookie)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "items": [],
        "attribution": "Source: Reserve Bank of India press releases (rbi.org.in)",
    }


async def test_the_newest_ten_come_back_with_short_summaries_and_links(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    long_summary = "word " * 118  # 590 characters, the most a stored summary can be
    await seed(
        session_factory,
        [
            item(
                n,
                f"Synthetic release {n}",
                published=datetime(2026, 9, n, 10, tzinfo=UTC),
                summary=long_summary,
            )
            for n in range(1, 13)
        ],
    )
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        body = (await client.get("/api/v1/feed", headers=cookie)).json()

    assert [i["title"] for i in body["items"]] == [
        f"Synthetic release {n}" for n in range(12, 2, -1)
    ]
    first = body["items"][0]
    assert first["url"] == "https://www.rbi.org.in/demo/12"
    assert first["is_fixture"] is False
    assert first["published_at"].startswith("2026-09-12T10:00:00")
    assert 250 <= len(first["summary"]) <= 305  # about 300 characters, cut at a word
    assert set(first) == {"title", "published_at", "summary", "url", "is_fixture"}


async def test_a_fixture_item_has_no_link_and_says_it_is_a_fixture(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    await seed(session_factory, [item(1, "Sample release", fixture=True, published=None)])
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        [only] = (await client.get("/api/v1/feed", headers=cookie)).json()["items"]
    assert (only["url"], only["is_fixture"], only["published_at"]) == (None, True, None)


# --- feed events in a stock's insights --------------------------------------------------------


async def test_a_feed_event_reaches_the_insights_with_an_rbi_citation(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    await seed(session_factory, [item(1, "Monetary penalty on DemoCo"), item(2, "Nothing here")])
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        response = await client.get("/api/v1/stocks/TCS/insights", headers=cookie)
    assert response.status_code == 200
    [event] = response.json()["events"]
    assert event["event_type"] == "regulatory_legal"
    assert (event["sentiment"], event["impact"], event["event_date"]) == (
        "negative",
        "medium",
        "2026-09-01",
    )
    assert event["summary"] == "Monetary penalty on DemoCo"
    assert event["citation"] == {
        "source": "rbi",
        "label": "RBI press release · 01 Sep 2026",
        "url": "https://www.rbi.org.in/demo/1",
        "quote": "Monetary penalty on DemoCo",
    }


async def test_a_feed_event_counts_towards_the_rolling_sentiment(
    session_factory: Factory,
) -> None:
    await seed(session_factory, [item(1, "Monetary penalty on DemoCo")])
    async with session_factory() as db:
        stock = await load_stock(db, "TCS")
    assert stock is not None
    assert [e.row.event_type for e in stock.events] == ["regulatory_legal"]
    assert stock.events[0].row.sentiment == "negative"


async def test_an_undated_or_fixture_feed_event_cites_without_a_date_or_a_link(
    session_factory: Factory,
) -> None:
    await seed(
        session_factory,
        [item(1, "DemoCo notice", published=None, fixture=True)],
    )
    async with session_factory() as db:
        stock = await load_stock(db, "TCS")
    assert stock is not None
    citation = stock.events[0].citation
    assert (citation.source, citation.label, citation.url) == ("rbi", "RBI press release", None)
    assert citation.quote == "DemoCo notice"


async def test_filing_events_are_unchanged_by_the_feed(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO documents (stock_id, title, sha256, size_bytes, blob_key, source, "
                "source_url, status, kind, period) SELECT id, 'DemoCo call', repeat('d', 64), 1, "
                "'documents/' || repeat('d', 64) || '.pdf', 'bse', "
                "'https://www.bseindia.com/x.pdf', 'completed', 'transcript', 'Jul 2026' "
                "FROM stocks WHERE symbol = 'TCS'"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO events (stock_id, document_id, page_number, event_type, sentiment, "
                "impact, event_date, date_source, summary, quote) SELECT stock_id, id, 7, "
                "'dividend', 'positive', 'low', DATE '2026-07-01', 'document', 'DemoCo dividend.', "
                "'The Board declared a dividend.' FROM documents"
            )
        )
    await seed(session_factory, [item(1, "DemoCo notice")])
    async with session_factory() as db:
        stock = await load_stock(db, "TCS")
    assert stock is not None
    by_source = {e.citation.source: e.citation for e in stock.events}
    assert set(by_source) == {"filing", "rbi"}
    assert by_source["filing"].label == "Earnings call · Jul 2026 · p.7"
    assert by_source["filing"].url == "https://www.bseindia.com/x.pdf#page=7"


async def test_an_unfinished_filing_still_hides_its_events_but_not_feed_events(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO documents (stock_id, title, sha256, size_bytes, blob_key, source, "
                "source_url, status) SELECT id, 'DemoCo call', repeat('e', 64), 1, "
                "'documents/' || repeat('e', 64) || '.pdf', 'bse', "
                "'https://www.bseindia.com/y.pdf', 'pending' FROM stocks WHERE symbol = 'TCS'"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO events (stock_id, document_id, page_number, event_type, sentiment, "
                "impact, event_date, date_source, summary, quote) SELECT stock_id, id, 1, "
                "'dividend', 'positive', 'low', DATE '2026-07-01', 'document', 's', 'q' "
                "FROM documents"
            )
        )
    await seed(session_factory, [item(1, "DemoCo notice")])
    async with session_factory() as db:
        stock = await load_stock(db, "TCS")
    assert stock is not None
    assert [e.citation.source for e in stock.events] == ["rbi"]
