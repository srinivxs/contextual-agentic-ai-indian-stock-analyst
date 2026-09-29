"""How fresh the data is, and the one-click update (the owner, 2026-09-29; app/data_status.py).

    GET  /api/v1/data/status    what every page shows: the dates the data is updated to, and
                                whether an update is still running
    POST /api/v1/data/refresh   queue every update its limits allow (202), and say per source
                                what happened; 429 within the hour after the last press

Signed-in only; the POST also needs the same Origin (ADR 013). The api never fetches: it only
queues jobs the worker runs, and only for sources switched on.
"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.auth.deps import current_user, require_same_origin
from app.auth.sessions import CurrentUser
from app.core.errors import AppError
from app.data_status import (
    DataStatus,
    FilingsOutcome,
    PricesOutcome,
    RbiOutcome,
    TooSoon,
    data_status,
    refresh,
)

router = APIRouter(prefix="/api/v1", tags=["data"])


class DataStatusOut(BaseModel):
    updating: bool
    filings_checked_at: str | None
    prices_to: str | None  # "2026-09-28"
    rbi_to: str | None
    next_update_at: str | None  # when "Update data" works again; None means now
    filings_on: bool
    prices_on: bool
    rbi_live: bool


class RefreshOut(BaseModel):
    filings: FilingsOutcome
    prices: PricesOutcome
    rbi: RbiOutcome
    status: DataStatusOut


def _status_out(status: DataStatus) -> DataStatusOut:
    return DataStatusOut(
        updating=status.updating,
        filings_checked_at=(
            status.filings_checked_at.isoformat() if status.filings_checked_at else None
        ),
        prices_to=status.prices_to.isoformat() if status.prices_to else None,
        rbi_to=status.rbi_to.isoformat() if status.rbi_to else None,
        next_update_at=status.next_update_at.isoformat() if status.next_update_at else None,
        filings_on=status.filings_on,
        prices_on=status.prices_on,
        rbi_live=status.rbi_live,
    )


@router.get("/data/status", summary="How fresh the stored data is")
async def status(
    request: Request, user: Annotated[CurrentUser, Depends(current_user)]
) -> DataStatusOut:
    async with request.app.state.session_factory() as db:
        found = await data_status(db, request.app.state.settings)
    return _status_out(found)


@router.post(
    "/data/refresh",
    status_code=202,
    summary="Update filings, prices and RBI releases now",
    dependencies=[Depends(require_same_origin)],
)
async def start_refresh(
    request: Request, user: Annotated[CurrentUser, Depends(current_user)]
) -> RefreshOut:
    settings = request.app.state.settings
    async with request.app.state.session_factory() as db:
        try:
            done = await refresh(db, settings, today=datetime.now(UTC).date(), user_id=user.id)
        except TooSoon as error:
            await db.rollback()
            raise AppError(
                status_code=429,
                code="rate_limited",
                message=f"Data can be updated again after {error.next_at.isoformat()}",
            ) from error
        await db.commit()
        found = await data_status(db, settings)
    return RefreshOut(
        filings=done.filings, prices=done.prices, rbi=done.rbi, status=_status_out(found)
    )
