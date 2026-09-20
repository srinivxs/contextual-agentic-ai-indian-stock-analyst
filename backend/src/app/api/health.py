"""Health endpoints.

``/api/healthz`` is a *liveness* probe: "is this process serving HTTP?". It deliberately touches no
dependency, so a database outage cannot make the load balancer kill otherwise-healthy tasks.

``/api/readyz`` is a *readiness* probe: "can this process do useful work right now?". It runs a real
``SELECT 1`` through the connection pool, so it fails when the database is down, unreachable, or
rejecting our credentials, and it fails fast (see ``db_ready_timeout_seconds``).
"""

import asyncio
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.engine import get_session

logger = logging.getLogger("app.health")

router = APIRouter(prefix="/api", tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadyResponse(BaseModel):
    status: Literal["ready"]


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")


@router.get("/readyz", summary="Readiness probe (checks the database)")
async def readyz(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> ReadyResponse:
    timeout = request.app.state.settings.db_ready_timeout_seconds
    try:
        async with asyncio.timeout(timeout):
            await session.execute(text("SELECT 1"))
    except Exception:
        # A readiness probe must never crash with a 500: any failure at all means "not ready".
        # The cause goes to the server log only; the response says nothing about hosts or users.
        logger.warning("readiness_check_failed", exc_info=True)
        raise AppError(status_code=503, code="not_ready", message="Database is not ready") from None
    return ReadyResponse(status="ready")
