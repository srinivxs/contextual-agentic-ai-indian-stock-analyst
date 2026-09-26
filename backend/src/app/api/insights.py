"""A stock's key facts, derived values, sentiment and events, each with its citation (P11c).

    GET /api/v1/stocks/{symbol}/insights

Signed in only. It loads the stock's stored facts and events (facts only from documents that are
fully ingested; screener.in facts always) and hands them to app/insights.py, which picks what to
show and attaches the citations. Amounts travel as strings, so a float never rounds a figure.
Nothing here calls an LLM: the page is computed on read from what the worker already verified.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import Row, text

from app.api.stocks import Symbol
from app.auth.deps import current_user
from app.auth.sessions import CurrentUser
from app.core.errors import AppError
from app.derived import EventRow, FactRow, Source, rolling_sentiment
from app.insights import (
    Citation,
    StoredEvent,
    StoredFact,
    derived_views,
    filing_citation,
    key_facts,
    recent_events,
    screener_citation,
)
from app.retrieval import filing_date

router = APIRouter(prefix="/api/v1", tags=["insights"])

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


class CitationOut(BaseModel):
    source: str
    label: str
    url: str | None
    quote: str | None


class KeyFactOut(BaseModel):
    metric: str
    label: str
    period: str
    basis: str
    currency: str | None
    unit: str
    value: str
    status: str
    corroborated_by: int
    citation: CitationOut
    disputed_by: list[CitationOut]


class DerivedOut(BaseModel):
    name: str
    label: str
    status: str
    value: str | None
    reason: str
    citations: list[CitationOut]


class SentimentOut(BaseModel):
    status: str
    score: float | None
    label: str | None
    events_counted: int


class EventOut(BaseModel):
    event_type: str
    sentiment: str
    impact: str
    event_date: str
    summary: str
    citation: CitationOut


class InsightsOut(BaseModel):
    symbol: str
    name: str
    is_financial: bool
    key_facts: list[KeyFactOut]
    derived: list[DerivedOut]
    sentiment: SentimentOut
    events: list[EventOut]


def _citation_out(citation: Citation) -> CitationOut:
    return CitationOut(**vars(citation))


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


def _amount(value: Decimal | None) -> str | None:
    """Plain digits, never scientific notation ("1E+2"): the page accepts only -?digits[.digits]."""
    return None if value is None else format(value, "f")


@router.get("/stocks/{symbol}/insights", summary="A stock's cited facts, derived values, events")
async def insights(
    symbol: Symbol, request: Request, user: Annotated[CurrentUser, Depends(current_user)]
) -> InsightsOut:
    async with request.app.state.session_factory() as db:
        stock = (await db.execute(_STOCK, {"symbol": symbol})).one_or_none()
        if stock is None:
            # Deliberately does not repeat the symbol the caller sent.
            raise AppError(status_code=404, code="not_found", message="No such stock")
        facts = [_stored_fact(row) for row in await db.execute(_FACTS, {"stock": stock.id})]
        events = [_stored_event(row) for row in await db.execute(_EVENTS, {"stock": stock.id})]

    sentiment = rolling_sentiment([event.row for event in events], as_of=date.today())
    return InsightsOut(
        symbol=stock.symbol,
        name=stock.name,
        is_financial=stock.is_financial,
        key_facts=[
            KeyFactOut(
                **{
                    **vars(fact),
                    "value": format(fact.value, "f"),
                    "citation": _citation_out(fact.citation),
                    "disputed_by": [_citation_out(c) for c in fact.disputed_by],
                }
            )
            for fact in key_facts(facts)
        ],
        derived=[
            DerivedOut(
                name=view.name,
                label=view.label,
                status=view.status,
                value=_amount(view.value),
                reason=view.reason,
                citations=[_citation_out(c) for c in view.citations],
            )
            for view in derived_views(facts, is_financial=stock.is_financial)
        ],
        sentiment=SentimentOut(
            status=sentiment.status,
            score=sentiment.score,
            label=sentiment.label,
            events_counted=len(sentiment.event_ids),
        ),
        events=[
            EventOut(
                event_type=event.row.event_type,
                sentiment=event.row.sentiment,
                impact=event.row.impact,
                event_date=event.row.event_date.isoformat(),
                summary=event.summary,
                citation=_citation_out(event.citation),
            )
            for event in recent_events(events)
        ],
    )
