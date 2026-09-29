"""Loading a stock's stored facts and events with their citations (P11c; shared with P12's chat).

Facts come only from documents that are fully ingested (and screener.in facts always); events the
same, and (P15) events from RBI feed items, cited to the press release. Each row becomes a
StoredFact / StoredEvent: the plain row app/derived.py computes with, plus the citation
app/insights.py shows. Plain SQL, one short transaction owned by the caller.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.derived import EventRow, FactRow, Source
from app.insights import Citation, StoredEvent, StoredFact, filing_citation, screener_citation
from app.prices.model import DailyPrice
from app.prices.store import load_prices
from app.retrieval import filing_date


@dataclass(frozen=True)
class StockRows:
    id: int
    symbol: str
    name: str
    is_financial: bool
    facts: list[StoredFact]
    events: list[StoredEvent]
    prices: list[DailyPrice] = field(default_factory=list)  # oldest first (ADR 025)


_STOCK = text("SELECT id, symbol, name, is_financial FROM stocks WHERE symbol = :symbol")
_FACTS = text(
    """
    SELECT f.id, f.metric, f.period, f.period_end, f.basis, f.currency, f.unit, f.value,
           f.source, f.page_number, f.quote, f.source_url, f.source_section, f.source_row,
           f.source_column, f.updated_at, d.kind, d.period AS document_period, d.title,
           d.source_url AS document_url
    FROM facts f LEFT JOIN documents d ON d.id = f.document_id
    WHERE f.stock_id = :stock AND (f.source = 'screener' OR d.status = 'completed')
    """
)
_EVENTS = text(
    """
    SELECT e.id, e.event_type, e.sentiment, e.impact, e.event_date, e.summary, e.quote,
           e.page_number, e.feed_item_id, d.kind, d.period AS document_period, d.title,
           d.source_url, f.canonical_url AS feed_url, f.published_at AS feed_published_at,
           f.is_fixture AS feed_is_fixture
    FROM events e
    LEFT JOIN documents d ON d.id = e.document_id
    LEFT JOIN feed_items f ON f.id = e.feed_item_id
    WHERE e.stock_id = :stock AND (e.feed_item_id IS NOT NULL OR d.status = 'completed')
    """
)


def _stored_fact(row: Row[Any]) -> StoredFact:
    source: Source
    source_date: date | None
    if row.source == "screener":
        updated: datetime = row.updated_at
        source, source_date = "screener", updated.date()
        citation = screener_citation(
            row.source_url, row.source_section, row.source_row, row.source_column
        )
    else:
        source, source_date = row.kind or "announcement", filing_date(row.document_period)
        citation = filing_citation(
            row.kind, row.document_period, row.title, row.document_url, row.page_number, row.quote
        )
    fact = FactRow(
        id=row.id,
        metric=row.metric,
        period=row.period,
        period_end=row.period_end,
        basis=row.basis,
        currency=row.currency,
        unit=row.unit,
        value=row.value,
        source=source,
        source_date=source_date,
    )
    return StoredFact(row=fact, citation=citation)


def feed_citation(
    published_at: datetime | None, url: str | None, is_fixture: bool, quote: str
) -> Citation:
    """ "RBI press release · 01 Sep 2026", linking to the release (a fixture item has no page)."""
    label = "RBI press release"
    if published_at is not None:
        label += f" · {published_at.strftime('%d %b %Y')}"
    return Citation(source="rbi", label=label, url=None if is_fixture else url, quote=quote)


def _stored_event(row: Row[Any]) -> StoredEvent:
    if row.feed_item_id is not None:
        citation = feed_citation(
            row.feed_published_at, row.feed_url, row.feed_is_fixture, row.quote
        )
    else:
        citation = filing_citation(
            row.kind, row.document_period, row.title, row.source_url, row.page_number, row.quote
        )
    return StoredEvent(
        row=EventRow(row.id, row.event_type, row.sentiment, row.impact, row.event_date),
        summary=row.summary,
        citation=citation,
    )


async def load_stock(db: AsyncSession, symbol: str) -> StockRows | None:
    """The stock with its stored facts, events and prices; None for an unknown symbol."""
    stock = (await db.execute(_STOCK, {"symbol": symbol})).one_or_none()
    if stock is None:
        return None
    facts = [_stored_fact(row) for row in await db.execute(_FACTS, {"stock": stock.id})]
    events = [_stored_event(row) for row in await db.execute(_EVENTS, {"stock": stock.id})]
    prices = await load_prices(db, stock.id)
    return StockRows(stock.id, stock.symbol, stock.name, stock.is_financial, facts, events, prices)
