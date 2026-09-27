"""Loading a stock's stored facts and events with their citations (P11c; shared with P12's chat).

Facts come only from documents that are fully ingested (and screener.in facts always); events the
same. Each row becomes a StoredFact / StoredEvent: the plain row app/derived.py computes with, plus
the citation app/insights.py shows. Plain SQL, one short transaction owned by the caller.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.derived import EventRow, FactRow, Source
from app.insights import StoredEvent, StoredFact, filing_citation, screener_citation
from app.retrieval import filing_date


@dataclass(frozen=True)
class StockRows:
    id: int
    symbol: str
    name: str
    is_financial: bool
    facts: list[StoredFact]
    events: list[StoredEvent]


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
           e.page_number, d.kind, d.period AS document_period, d.title, d.source_url
    FROM events e JOIN documents d ON d.id = e.document_id
    WHERE e.stock_id = :stock AND d.status = 'completed'
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


def _stored_event(row: Row[Any]) -> StoredEvent:
    return StoredEvent(
        row=EventRow(row.id, row.event_type, row.sentiment, row.impact, row.event_date),
        summary=row.summary,
        citation=filing_citation(
            row.kind, row.document_period, row.title, row.source_url, row.page_number, row.quote
        ),
    )


async def load_stock(db: AsyncSession, symbol: str) -> StockRows | None:
    """The stock and its stored facts and events; None for an unknown symbol."""
    stock = (await db.execute(_STOCK, {"symbol": symbol})).one_or_none()
    if stock is None:
        return None
    facts = [_stored_fact(row) for row in await db.execute(_FACTS, {"stock": stock.id})]
    events = [_stored_event(row) for row in await db.execute(_EVENTS, {"stock": stock.id})]
    return StockRows(stock.id, stock.symbol, stock.name, stock.is_financial, facts, events)
