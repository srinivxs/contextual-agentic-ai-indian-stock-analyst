"""A stock's end-of-day share prices, computed on read (ADR 025, ADR 009).

    GET /api/v1/stocks/{symbol}/prices

Signed in only. One short read loads the stock's stored prices and facts; app/prices/derived.py
does the arithmetic outside the transaction: the history adjusted for bonus issues and splits
(the last 365 days), returns over 1, 3, 6 and 12 months, one-year volatility, P/E and dividend
yield. Every figure that is not a price carries its citations: the BSE daily price file it used
and, for P/E and yield, the per-share fact. Numbers travel as plain strings. Nothing is stored
or converted here, and nothing here is live: BSE publishes one file after each trading day.
"""

from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.insights import CitationOut
from app.api.stocks import Symbol
from app.auth.deps import current_user
from app.auth.sessions import CurrentUser
from app.core.errors import AppError
from app.insights import Citation, key_facts
from app.insights_store import load_stock
from app.prices.derived import PriceValue, adjusted_closes, price_citation, snapshot
from app.prices.model import DailyPrice

router = APIRouter(prefix="/api/v1", tags=["prices"])

SOURCE = "BSE daily price file (end of day, not live)"
HISTORY_DAYS = 365
TWO_PLACES = Decimal("0.01")


class LatestOut(BaseModel):
    date: str
    close: str
    prev_close: str
    change_pct: str
    citation: CitationOut


class PointOut(BaseModel):
    date: str
    close: str


class ValueOut(BaseModel):
    value: str | None
    reason: str
    status: str
    citations: list[CitationOut]


class ActionOut(BaseModel):
    date: str
    factor: str


class PricesOut(BaseModel):
    symbol: str
    source: str
    latest: LatestOut | None
    history: list[PointOut]
    returns: dict[str, str | None]
    volatility_1y: str | None
    pe: ValueOut
    dividend_yield: ValueOut
    actions: list[ActionOut]


def _plain(value: Decimal | None) -> str | None:
    """Plain digits, never scientific notation."""
    return None if value is None else format(value, "f")


def _citation_out(citation: Citation) -> CitationOut:
    return CitationOut(**vars(citation))


def _value_out(result: PriceValue, latest: DailyPrice | None) -> ValueOut:
    citations = [] if latest is None else [price_citation(latest.trade_date)]
    if latest is not None and result.fact is not None:
        citations.append(result.fact.citation)
    return ValueOut(
        value=_plain(result.value),
        reason=result.reason,
        status=result.status,
        citations=[_citation_out(citation) for citation in citations],
    )


def _latest_out(latest: DailyPrice) -> LatestOut:
    # BSE's previous close is already adjusted for an action today, so this is the real change
    change = ((latest.close / latest.prev_close - 1) * 100).quantize(Decimal("0.1"), ROUND_HALF_UP)
    return LatestOut(
        date=latest.trade_date.isoformat(),
        close=format(latest.close, "f"),
        prev_close=format(latest.prev_close, "f"),
        change_pct=format(change, "f"),
        citation=_citation_out(price_citation(latest.trade_date)),
    )


@router.get("/stocks/{symbol}/prices", summary="A stock's end-of-day prices and what they imply")
async def prices(
    symbol: Symbol, request: Request, user: Annotated[CurrentUser, Depends(current_user)]
) -> PricesOut:
    async with request.app.state.session_factory() as db:
        stock = await load_stock(db, symbol)
    if stock is None:
        # Deliberately does not repeat the symbol the caller sent.
        raise AppError(status_code=404, code="not_found", message="No such stock")

    rows = stock.prices
    snap = snapshot(rows, key_facts(stock.facts))  # the same summary the chat shows
    latest = snap.latest
    adjusted = adjusted_closes(rows)
    returns = {name: _plain(value) for name, value in snap.returns.items()}
    history: list[PointOut] = []
    if latest is not None:
        since = latest.trade_date - timedelta(days=HISTORY_DAYS)
        history = [
            PointOut(
                date=point.trade_date.isoformat(),
                close=format(point.close.quantize(TWO_PLACES, ROUND_HALF_UP), "f"),
            )
            for point in adjusted
            if point.trade_date >= since
        ]
    return PricesOut(
        symbol=stock.symbol,
        source=SOURCE,
        latest=None if latest is None else _latest_out(latest),
        history=history,
        returns=returns,
        volatility_1y=_plain(snap.volatility),
        pe=_value_out(snap.pe, latest),
        dividend_yield=_value_out(snap.dividend_yield, latest),
        actions=[
            ActionOut(date=a.trade_date.isoformat(), factor=format(a.factor.normalize(), "f"))
            for a in snap.actions
        ],
    )
