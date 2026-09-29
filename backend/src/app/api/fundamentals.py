"""The stock page's Fundamentals card (the owner, 2026-09-30; app/fundamentals.py).

    GET /api/v1/stocks/{symbol}/fundamentals   ten values, each with its status, unit, source and
                                               note; when screener.in's figures were read, and the
                                               page they are from

Signed-in only. Values travel as strings, like every amount in this API, so a float never rounds
one. Checks run in the order session, symbol, stock exists (ADR 013).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.api.stocks import Symbol
from app.auth.deps import current_user
from app.auth.sessions import CurrentUser
from app.core.errors import AppError
from app.fundamentals import Fundamental, fundamentals, load_ratios
from app.insights import derived_views
from app.insights_store import load_stock

router = APIRouter(prefix="/api/v1", tags=["fundamentals"])


class FundamentalOut(BaseModel):
    name: str
    label: str
    status: str
    value: str | None
    unit: str
    source: str
    note: str | None


class FundamentalsOut(BaseModel):
    symbol: str
    as_of: str | None  # when screener.in's figures were read; None before the first read
    source_url: str | None
    items: list[FundamentalOut]


def _item_out(item: Fundamental) -> FundamentalOut:
    return FundamentalOut(
        name=item.name,
        label=item.label,
        status=item.status,
        value=None if item.value is None else format(item.value, "f"),
        unit=item.unit,
        source=item.source,
        note=item.note,
    )


@router.get("/stocks/{symbol}/fundamentals", summary="A stock's fundamentals card")
async def stock_fundamentals(
    symbol: Symbol, request: Request, user: Annotated[CurrentUser, Depends(current_user)]
) -> FundamentalsOut:
    async with request.app.state.session_factory() as db:
        stock = await load_stock(db, symbol)
        if stock is None:
            # Deliberately does not repeat the symbol the caller sent.
            raise AppError(status_code=404, code="not_found", message="No such stock")
        ratios = await load_ratios(db, stock.id)
    debt = derived_views(stock.facts, is_financial=stock.is_financial)[0]
    return FundamentalsOut(
        symbol=stock.symbol,
        as_of=ratios.fetched_at.isoformat() if ratios else None,
        source_url=ratios.source_url if ratios else None,
        items=[_item_out(item) for item in fundamentals(ratios, debt)],
    )
