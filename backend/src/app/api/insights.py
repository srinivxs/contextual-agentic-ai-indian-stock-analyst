"""A stock's key facts, derived values, sentiment and events, each with its citation (P11c).

    GET /api/v1/stocks/{symbol}/insights

Signed in only. It loads the stock's stored facts and events (facts only from documents that are
fully ingested; screener.in facts always) and hands them to app/insights.py, which picks what to
show and attaches the citations. Amounts travel as strings, so a float never rounds a figure.
Nothing here calls an LLM: the page is computed on read from what the worker already verified.
"""

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.stocks import Symbol
from app.auth.deps import current_user
from app.auth.sessions import CurrentUser
from app.clock import india_today
from app.core.errors import AppError
from app.derived import rolling_sentiment
from app.insights import (
    Citation,
    derived_views,
    key_facts,
    recent_events,
)
from app.insights_store import load_stock

router = APIRouter(prefix="/api/v1", tags=["insights"])


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


def _amount(value: Decimal | None) -> str | None:
    """Plain digits, never scientific notation ("1E+2"): the page accepts only -?digits[.digits]."""
    return None if value is None else format(value, "f")


@router.get("/stocks/{symbol}/insights", summary="A stock's cited facts, derived values, events")
async def insights(
    symbol: Symbol, request: Request, user: Annotated[CurrentUser, Depends(current_user)]
) -> InsightsOut:
    async with request.app.state.session_factory() as db:
        stock = await load_stock(db, symbol)
    if stock is None:
        # Deliberately does not repeat the symbol the caller sent.
        raise AppError(status_code=404, code="not_found", message="No such stock")
    facts, events = stock.facts, stock.events

    sentiment = rolling_sentiment([event.row for event in events], as_of=india_today())
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
