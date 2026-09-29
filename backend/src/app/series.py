"""One metric over the years, from one source (redesign; ADR 020, the P12c lesson in ADR 021).

Like with like: a series mixing sources would show a change in definition as a change in the
business, so it holds ONLY screener.in's consolidated, rupee, full-year figures. Two readers use
it: the Home chart (app/api/series.py) and the chat's year-by-year table (app/chat/tables.py).
Nothing is computed or converted here.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MAX_POINTS = 10

_POINTS = text(
    """
    SELECT period, value, source_url, source_section, source_row, source_column
    FROM facts
    WHERE stock_id = :stock AND metric = :metric AND source = 'screener'
      AND basis = 'consolidated' AND unit = 'INR_CRORE' AND period ~ '^FY[0-9]{4}$'
    ORDER BY period_end DESC
    LIMIT :limit
    """
)


@dataclass(frozen=True)
class SeriesPoint:
    period: str  # "FY2026"
    value: Decimal  # rupees crore
    source_url: str  # the screener.in company page
    source_section: str
    source_row: str
    source_column: str


async def load_series(
    db: AsyncSession, stock_id: int, metric: str, limit: int = MAX_POINTS
) -> list[SeriesPoint]:
    """The newest ``limit`` full years of the metric, oldest first."""
    rows = (await db.execute(_POINTS, {"stock": stock_id, "metric": metric, "limit": limit})).all()
    return [
        SeriesPoint(
            period=row.period,
            value=row.value,
            source_url=row.source_url,
            source_section=row.source_section,
            source_row=row.source_row,
            source_column=row.source_column,
        )
        for row in reversed(rows)
    ]
