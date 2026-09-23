"""Checking a stock for new filings on demand: the "Check for new filings" button (ADR 018).

    GET  /api/v1/stocks/{symbol}/filings/check   what the page shows: checking? last checked? when
                                                 can it be checked again?
    POST /api/v1/stocks/{symbol}/filings/check   queue a check now

The api never reads screener.in or BSE itself. It only queues a ``discover_filings`` job for the
stock; the worker reads the links and downloads just the filings not stored yet. At most one check
per stock per hour (CHECK_COOLDOWN_HOURS), for everyone together.

POST answers 202 whenever a check is under way, whether this request queued it or someone else's
did; 429 when the stock was checked within the hour and nothing is running; 409 when automatic
filings are switched off (the worker could not run the job). Checks run in the order Origin,
session, symbol, stock exists (ADR 013).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse

from app.api.stocks import Symbol
from app.auth.deps import current_user, require_same_origin
from app.auth.sessions import CurrentUser
from app.core.errors import AppError
from app.documents import stock_id
from app.filings import CHECK_COOLDOWN_HOURS, FilingCheck, enqueue_discovery, filing_check

router = APIRouter(prefix="/api/v1", tags=["filings"])


class FilingCheckOut(BaseModel):
    enabled: bool
    checking: bool
    last_checked_at: str | None
    next_check_at: str | None


def _out(check: FilingCheck, *, enabled: bool) -> FilingCheckOut:
    return FilingCheckOut(
        enabled=enabled,
        checking=check.checking,
        last_checked_at=check.last_checked_at.isoformat() if check.last_checked_at else None,
        next_check_at=check.next_check_at.isoformat() if check.next_check_at else None,
    )


def _no_such_stock() -> AppError:
    # Deliberately does not repeat the symbol the caller sent.
    return AppError(status_code=404, code="not_found", message="No such stock")


@router.get("/stocks/{symbol}/filings/check", summary="When this stock was last checked")
async def check_status(
    symbol: Symbol, request: Request, user: Annotated[CurrentUser, Depends(current_user)]
) -> FilingCheckOut:
    async with request.app.state.session_factory() as db:
        if await stock_id(db, symbol) is None:
            raise _no_such_stock()
        check = await filing_check(db, symbol)
    return _out(check, enabled=request.app.state.settings.filings_discovery)


@router.post(
    "/stocks/{symbol}/filings/check",
    status_code=202,
    summary="Check this stock for new filings now",
    dependencies=[Depends(require_same_origin)],
)
async def start_check(
    symbol: Symbol, request: Request, user: Annotated[CurrentUser, Depends(current_user)]
) -> JSONResponse:
    async with request.app.state.session_factory() as db:
        if await stock_id(db, symbol) is None:
            raise _no_such_stock()
        if not request.app.state.settings.filings_discovery:
            raise AppError(
                status_code=409, code="conflict", message="Automatic filings are switched off"
            )
        await enqueue_discovery(db, every_hours=CHECK_COOLDOWN_HOURS, symbol=symbol)
        await db.commit()
        check = await filing_check(db, symbol)
    if not check.checking:
        raise AppError(
            status_code=429,
            code="rate_limited",
            message="This stock was checked less than an hour ago",
        )
    return JSONResponse(status_code=202, content=_out(check, enabled=True).model_dump())
