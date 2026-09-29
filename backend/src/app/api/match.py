"""How the stocks fit the caller's profile (P14).

    GET /api/v1/match

Signed in only. One short read transaction loads the caller's remembered profile and the three
stocks' stored facts and events; the deterministic rules (app/matching/rules.py) then judge each
stock outside it. No LLM: every reason is a stored figure against a stated preference.
"""

from dataclasses import asdict
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import current_user
from app.auth.sessions import CurrentUser
from app.insights_store import StockRows, load_stock
from app.matching.model import Reason, StockMatch
from app.matching.rules import match_all
from app.memory.store import get_profile
from app.memory.vocabulary import StoredPreference

router = APIRouter(prefix="/api/v1", tags=["match"])

DISCLAIMER = (
    "Not investment advice. The rules compare stored figures and end-of-day share prices with "
    "your stated preferences."
)


class CitationOut(BaseModel):
    source: str
    label: str
    url: str | None
    quote: str | None


class ReasonOut(BaseModel):
    criterion: str
    preference: str
    hard: bool
    outcome: str
    text: str
    citations: list[CitationOut]


class StockMatchOut(BaseModel):
    symbol: str
    name: str
    status: str
    reasons: list[ReasonOut]
    cautions: list[ReasonOut]


class MatchOut(BaseModel):
    profile_empty: bool
    stocks: list[StockMatchOut]
    disclaimer: str


def _reason_out(reason: Reason) -> ReasonOut:
    return ReasonOut(
        criterion=reason.criterion,
        preference=reason.preference,
        hard=reason.hard,
        outcome=reason.outcome,
        text=reason.text,
        citations=[CitationOut(**asdict(c)) for c in reason.citations],
    )


def _match_out(match: StockMatch) -> StockMatchOut:
    return StockMatchOut(
        symbol=match.symbol,
        name=match.name,
        status=match.status,
        reasons=[_reason_out(r) for r in match.reasons],
        cautions=[_reason_out(r) for r in match.cautions],
    )


async def _load(db: AsyncSession, user_id: UUID) -> tuple[list[StoredPreference], list[StockRows]]:
    """The caller's profile and the stocks (in their usual order) with their stored rows."""
    profile = await get_profile(db, user_id)
    symbols = list((await db.execute(text("SELECT symbol FROM stocks ORDER BY id"))).scalars())
    loaded = [await load_stock(db, symbol) for symbol in symbols]
    stocks = [stock for stock in loaded if stock is not None]
    return profile, stocks


@router.get("/match", summary="How each stock fits your remembered profile")
async def match(request: Request, user: Annotated[CurrentUser, Depends(current_user)]) -> MatchOut:
    async with request.app.state.session_factory() as db:
        profile, stocks = await _load(db, user.id)
    matches = match_all(profile, stocks, today=date.today())
    return MatchOut(
        profile_empty=not profile,
        stocks=[_match_out(m) for m in matches],
        disclaimer=DISCLAIMER,
    )
