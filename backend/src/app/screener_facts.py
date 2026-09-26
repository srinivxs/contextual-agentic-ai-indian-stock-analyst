"""screener.in's fundamentals, stored as cited facts (P11, ADR 020: the owner's decision).

The daily discovery (app/filings.py) already reads each stock's screener.in company page for filing
links. The same page's fundamentals table is parsed by code (app/screener_numbers.py; no LLM) and
each figure is stored in ``facts`` with source 'screener' and its citation: the page URL and the
section, row and column it was read from.

One screener fact per (stock, metric, period, basis): a new day's read updates the figure in place
(screener revises recent numbers). A page that yields nothing (a layout change) stores nothing and
deletes nothing: the history stays until the parser is fixed.
"""

from typing import Any, cast

from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.screener_numbers import map_to_vocabulary, parse_fundamentals

_UPSERT = text(
    """
    INSERT INTO facts (stock_id, source, source_url, source_section, source_row, source_column,
                       metric, period, period_end, basis, currency, unit, value)
    VALUES (:stock_id, 'screener', :url, :section, :row, :column,
            :metric, :period, :period_end, :basis, :currency, :unit, :value)
    ON CONFLICT (stock_id, metric, period, basis) WHERE source = 'screener'
    DO UPDATE SET value = EXCLUDED.value,
                  period_end = EXCLUDED.period_end,
                  currency = EXCLUDED.currency,
                  unit = EXCLUDED.unit,
                  source_url = EXCLUDED.source_url,
                  source_section = EXCLUDED.source_section,
                  source_row = EXCLUDED.source_row,
                  source_column = EXCLUDED.source_column,
                  updated_at = now()
    """
)


async def store_screener_facts(
    db: AsyncSession, *, stock_id: int, is_financial: bool, page: str, url: str
) -> int:
    """Store (or refresh) every fundamentals figure the page yields. Returns how many."""
    facts = map_to_vocabulary(
        parse_fundamentals(page), is_financial=is_financial, view="consolidated"
    )
    stored = 0
    for fact in facts:
        result = cast(
            "CursorResult[Any]",
            await db.execute(
                _UPSERT,
                {
                    "stock_id": stock_id,
                    "url": url,
                    "section": fact.section,
                    "row": fact.row_label,
                    "column": fact.column_label,
                    "metric": fact.metric,
                    "period": fact.period,
                    "period_end": fact.period_end,
                    "basis": fact.basis,
                    "currency": fact.currency,
                    "unit": fact.unit,
                    "value": fact.value,
                },
            ),
        )
        stored += result.rowcount
    return stored
