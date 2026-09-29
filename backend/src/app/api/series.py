"""One metric over the years, for the Home page's chart (redesign).

    GET /api/v1/stocks/{symbol}/series?metric=net_profit

Signed in only. Like with like (ADR 020; the P12c lesson in ADR 021): a chart that mixed sources
would show a change in definition as a change in the business, so the series holds ONLY
screener.in's consolidated, rupee, full-year figures, the newest ten, oldest first. Each point
carries the citation of its screener.in row and column. Nothing is computed or converted here.
"""

from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import text

from app.api.insights import CitationOut
from app.api.stocks import Symbol
from app.auth.deps import current_user
from app.auth.sessions import CurrentUser
from app.core.errors import AppError
from app.insights import METRIC_LABELS, screener_citation

router = APIRouter(prefix="/api/v1", tags=["series"])

SeriesMetric = Literal["net_profit", "revenue_from_operations", "net_interest_income"]
MAX_POINTS = 10

_STOCK_ID = text("SELECT id FROM stocks WHERE symbol = :symbol")
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


class PointOut(BaseModel):
    period: str
    value: str
    citation: CitationOut


class SeriesOut(BaseModel):
    symbol: str
    metric: str
    label: str
    unit: str
    source: str
    points: list[PointOut]


def _plain(value: Decimal) -> str:
    """Plain digits ("32447", "100.5", "-3"): never "1E+2", no trailing zeros."""
    return format(value.normalize(), "f")


@router.get("/stocks/{symbol}/series", summary="A metric over the years, one source")
async def series(
    symbol: Symbol,
    metric: Annotated[SeriesMetric, Query()],
    request: Request,
    user: Annotated[CurrentUser, Depends(current_user)],
) -> SeriesOut:
    async with request.app.state.session_factory() as db:
        stock = (await db.execute(_STOCK_ID, {"symbol": symbol})).scalar_one_or_none()
        if stock is None:
            # Deliberately does not repeat the symbol the caller sent.
            raise AppError(status_code=404, code="not_found", message="No such stock")
        rows = (
            await db.execute(_POINTS, {"stock": stock, "metric": metric, "limit": MAX_POINTS})
        ).all()
    return SeriesOut(
        symbol=symbol,
        metric=metric,
        label=METRIC_LABELS[metric],
        unit="INR_CRORE",
        source="screener.in, consolidated",
        points=[
            PointOut(
                period=row.period,
                value=_plain(row.value),
                citation=CitationOut(
                    **vars(
                        screener_citation(
                            row.source_url, row.source_section, row.source_row, row.source_column
                        )
                    )
                ),
            )
            for row in reversed(rows)
        ],
    )
