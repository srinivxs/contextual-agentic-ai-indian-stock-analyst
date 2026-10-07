"""The Fundamentals card of the stock page (the owner, 2026-09-30).

Ten values, in the owner's order. Most are screener.in's "top ratios", read by code from the
company page the worker already fetches once a day (app/screener_numbers.py's parse_top_ratios,
ADR 018 and 020) and stored one row per stock (``screener_ratios``, replaced on each read, so
"Update data" refreshes them). The rest are computed on read (ADR 009), never stored:

    Mkt Cap          screener.in "Market Cap" (₹ crore)
    ROE              screener.in "ROE" (%)
    P/E Ratio (TTM)  screener.in "Stock P/E" (its current price over twelve months' earnings)
    EPS (TTM)        computed: screener.in's current price / its P/E, the same moment's figures
    P/B Ratio        computed: screener.in's current price / its book value per share
    Div Yield        screener.in "Dividend Yield" (%)
    Industry P/E     not available: the company page this app reads does not show it, and no
                     other page is read (ADR 018)
    Book Value       screener.in "Book Value" (₹ per share)
    Debt to Equity   ours: total borrowings / total equity from stored facts (app/derived.py);
                     "not applicable" for a bank
    Face Value       screener.in "Face Value" (₹ per share)

A value the page does not give is "not available", never zero; a computed value whose input is
missing or not positive is "not available" too. screener.in's figures are as of its read (the
card says when), not BSE's end-of-day close shown in the share price card.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.insights import DerivedView

Unit = Literal["INR_CRORE", "INR_PER_SHARE", "PERCENT", "RATIO"]
Status = Literal["ok", "not_available", "not_applicable"]
Source = Literal["screener", "computed", "none"]
_HUNDREDTH = Decimal("0.01")
_NOT_ON_PAGE = "Not on the stored screener.in page."


@dataclass(frozen=True)
class Ratios:
    """One stock's stored screener.in top ratios (a missing item is None)."""

    market_cap_crore: Decimal | None
    current_price: Decimal | None
    stock_pe: Decimal | None
    book_value: Decimal | None
    dividend_yield_percent: Decimal | None
    roce_percent: Decimal | None
    roe_percent: Decimal | None
    face_value: Decimal | None
    source_url: str
    fetched_at: datetime


@dataclass(frozen=True)
class Fundamental:
    name: str
    label: str
    status: Status
    value: Decimal | None
    unit: Unit
    source: Source  # where the value is from: screener.in's page, computed, or nowhere
    note: str | None  # how it was computed, or why there is none


def _plain(value: Decimal) -> str:
    text_value = format(value, "f")
    return text_value.rstrip("0").rstrip(".") if "." in text_value else text_value


def _stored(name: str, label: str, value: Decimal | None, unit: Unit) -> Fundamental:
    if value is None:
        return Fundamental(name, label, "not_available", None, unit, "none", _NOT_ON_PAGE)
    return Fundamental(name, label, "ok", value, unit, "screener", None)


def _divided(
    name: str, label: str, unit: Unit, top: Decimal | None, bottom: Decimal | None, how: str
) -> Fundamental:
    if top is None or bottom is None or bottom <= 0:
        note = "Not available: an input is missing or not positive."
        return Fundamental(name, label, "not_available", None, unit, "none", note)
    value = (top / bottom).quantize(_HUNDREDTH, rounding=ROUND_HALF_UP)
    return Fundamental(name, label, "ok", value, unit, "computed", how)


def fundamentals(ratios: Ratios | None, debt: DerivedView | None) -> list[Fundamental]:
    """The ten values, in the owner's order."""
    r = ratios
    price = r.current_price if r else None
    pe = r.stock_pe if r else None
    book = r.book_value if r else None
    eps_how = (
        f"Computed: current price ₹{_plain(price)} divided by P/E {_plain(pe)}."
        if price is not None and pe is not None
        else None
    )
    pb_how = (
        f"Computed: current price ₹{_plain(price)} divided by book value ₹{_plain(book)}."
        if price is not None and book is not None
        else None
    )
    if debt is not None and debt.status == "ok" and debt.value is not None:
        debt_item = Fundamental(
            "debt_to_equity", "Debt to Equity", "ok", debt.value, "RATIO", "computed", debt.reason
        )
    else:
        status: Status = (
            "not_applicable" if debt and debt.status == "not_applicable" else "not_available"
        )
        debt_item = Fundamental(
            "debt_to_equity", "Debt to Equity", status, None, "RATIO", "none",
            debt.reason if debt else None,
        )  # fmt: skip
    industry_note = "Not in the data: the screener.in company page this app reads does not show it."
    return [
        _stored("market_cap", "Mkt Cap", r.market_cap_crore if r else None, "INR_CRORE"),
        _stored("roe", "ROE", r.roe_percent if r else None, "PERCENT"),
        _stored("pe_ttm", "P/E Ratio (TTM)", pe, "RATIO"),
        _divided("eps_ttm", "EPS (TTM)", "INR_PER_SHARE", price, pe, eps_how or ""),
        _divided("pb", "P/B Ratio", "RATIO", price, book, pb_how or ""),
        _stored("dividend_yield", "Div Yield", r.dividend_yield_percent if r else None, "PERCENT"),
        Fundamental(
            "industry_pe", "Industry P/E", "not_available", None, "RATIO", "none", industry_note
        ),
        _stored("book_value", "Book Value", book, "INR_PER_SHARE"),
        debt_item,
        _stored("face_value", "Face Value", r.face_value if r else None, "INR_PER_SHARE"),
    ]  # fmt: skip


_LOAD = text(
    """
    SELECT market_cap_crore, current_price, stock_pe, book_value, dividend_yield_percent,
           roce_percent, roe_percent, face_value, source_url, fetched_at
    FROM screener_ratios WHERE stock_id = :stock_id
    """
)


async def load_ratios(db: AsyncSession, stock_id: int) -> Ratios | None:
    row = (await db.execute(_LOAD, {"stock_id": stock_id})).one_or_none()
    if row is None:
        return None
    return Ratios(**row._mapping)
