"""The stocks list and the follow endpoints.

Every route needs a session. On the state-changing ones the checks run in a fixed order:
**Origin, then session, then symbol validation.** So a cross-site page learns nothing (not even
whether you are signed in), and an anonymous caller learns nothing about which symbols are valid.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel
from starlette.responses import Response

from app.auth.deps import current_user, require_same_origin
from app.auth.sessions import CurrentUser
from app.core.errors import AppError
from app.stocks import follow, list_stocks, unfollow

router = APIRouter(prefix="/api/v1", tags=["stocks"])

# The same shape the database enforces on stocks.symbol (see migration 0001).
Symbol = Annotated[str, Path(pattern=r"^[A-Z0-9&-]{1,20}$")]


class StockOut(BaseModel):
    symbol: str
    name: str
    bse_code: str
    sector: str
    followed: bool


class StocksResponse(BaseModel):
    # An object with `items`, not a bare list, so a field can be added later without breaking
    # callers. No pagination: the universe is fixed at three stocks (ADR 007).
    items: list[StockOut]


def _no_such_stock() -> AppError:
    # Deliberately does not repeat the symbol the caller sent.
    return AppError(status_code=404, code="not_found", message="No such stock")


@router.get("/stocks", summary="The stocks, and which ones you follow")
async def stocks(
    request: Request, user: Annotated[CurrentUser, Depends(current_user)]
) -> StocksResponse:
    async with request.app.state.session_factory() as db:
        views = await list_stocks(db, user.id)
    return StocksResponse(items=[StockOut(**vars(view)) for view in views])


@router.put(
    "/stocks/{symbol}/follow",
    status_code=204,
    summary="Follow a stock (idempotent)",
    dependencies=[Depends(require_same_origin)],
)
async def follow_stock(
    symbol: Symbol, request: Request, user: Annotated[CurrentUser, Depends(current_user)]
) -> Response:
    async with request.app.state.session_factory() as db:
        found = await follow(db, user.id, symbol)
        await db.commit()
    if not found:
        raise _no_such_stock()
    return Response(status_code=204)


@router.delete(
    "/stocks/{symbol}/follow",
    status_code=204,
    summary="Unfollow a stock (idempotent)",
    dependencies=[Depends(require_same_origin)],
)
async def unfollow_stock(
    symbol: Symbol, request: Request, user: Annotated[CurrentUser, Depends(current_user)]
) -> Response:
    async with request.app.state.session_factory() as db:
        found = await unfollow(db, user.id, symbol)
        await db.commit()
    if not found:
        raise _no_such_stock()
    return Response(status_code=204)
