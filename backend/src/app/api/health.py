"""Health endpoints.

``/api/healthz`` is a *liveness* probe: "is this process serving HTTP?". It deliberately touches no
dependency, so a database outage cannot make the load balancer kill otherwise-healthy tasks.
A readiness probe (``/api/readyz``, checks the database) arrives with the database milestone.
"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]


@router.get("/healthz", summary="Liveness probe")
async def healthz() -> HealthResponse:
    return HealthResponse(status="ok")
