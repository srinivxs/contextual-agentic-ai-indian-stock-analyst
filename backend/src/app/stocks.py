"""The three stocks and who follows them.

Plain SQL, like the rest of the data access (ADR 013): one small table does not justify a second
layer of ORM models. None of these functions commits; the caller owns the transaction.

Every function takes the user id from the authenticated session and nothing else, so a request can
never act on someone else's follows.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Stable order: the order the stocks were seeded in.
_LIST_STOCKS = text(
    "SELECT s.symbol, s.name, s.bse_code, s.sector, (f.user_id IS NOT NULL) AS followed "
    "FROM stocks s LEFT JOIN user_follows f ON f.stock_id = s.id AND f.user_id = :user_id "
    "ORDER BY s.id"
)

_STOCK_ID = text("SELECT id FROM stocks WHERE symbol = :symbol")

# The primary key (user_id, stock_id) is what makes concurrent duplicates converge on one row.
_FOLLOW = text(
    "INSERT INTO user_follows (user_id, stock_id) VALUES (:user_id, :stock_id) "
    "ON CONFLICT (user_id, stock_id) DO NOTHING"
)

_UNFOLLOW = text("DELETE FROM user_follows WHERE user_id = :user_id AND stock_id = :stock_id")


@dataclass(frozen=True)
class StockView:
    """A stock as one user sees it. Internal ids and our own flags stay in the database."""

    symbol: str
    name: str
    bse_code: str
    sector: str
    followed: bool


async def list_stocks(db: AsyncSession, user_id: UUID) -> list[StockView]:
    result = await db.execute(_LIST_STOCKS, {"user_id": user_id})
    return [
        StockView(
            symbol=row.symbol,
            name=row.name,
            bse_code=row.bse_code,
            sector=row.sector,
            followed=row.followed,
        )
        for row in result
    ]


async def _stock_id(db: AsyncSession, symbol: str) -> int | None:
    found: int | None = (await db.execute(_STOCK_ID, {"symbol": symbol})).scalar_one_or_none()
    return found


async def follow(db: AsyncSession, user_id: UUID, symbol: str) -> bool:
    """Follow the stock. Idempotent. Returns False if there is no such stock."""
    stock_id = await _stock_id(db, symbol)
    if stock_id is None:
        return False
    await db.execute(_FOLLOW, {"user_id": user_id, "stock_id": stock_id})
    return True


async def unfollow(db: AsyncSession, user_id: UUID, symbol: str) -> bool:
    """Stop following the stock. Idempotent. Returns False if there is no such stock."""
    stock_id = await _stock_id(db, symbol)
    if stock_id is None:
        return False
    await db.execute(_UNFOLLOW, {"user_id": user_id, "stock_id": stock_id})
    return True
