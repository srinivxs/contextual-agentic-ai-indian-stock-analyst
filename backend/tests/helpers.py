"""Plain helpers shared by tests (fixtures live in conftest.py)."""

import asyncio
import io
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI

from app.core.config import Settings
from app.core.context import get_request_id
from app.core.errors import AppError
from app.main import create_app

# Never connected to: unit and API tests replace the database dependency, and the engine is lazy.
TEST_DATABASE_URL = "postgresql+asyncpg://nobody:not-a-real-password@localhost:5432/not_a_database"


def build_settings(**overrides: Any) -> Settings:
    """Settings for tests. `_env_file=None` so a developer's real .env can never leak in."""
    values: dict[str, Any] = {
        "app_env": "test",
        "log_level": "INFO",
        "database_url": TEST_DATABASE_URL,
        **overrides,
    }
    return Settings(_env_file=None, **values)


@asynccontextmanager
async def running_app(settings: Settings) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    """A real app with its lifespan running (so shutdown disposes the engine) and a client."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield app, client


def parse_log_lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def add_test_routes(app: FastAPI) -> None:
    """Routes that exist only in tests, to exercise middleware and error handling."""

    @app.get("/_test/ok")
    async def ok() -> dict[str, str]:
        return {"result": "ok"}

    @app.get("/_test/conflict")
    async def conflict() -> None:
        raise AppError(status_code=409, code="conflict", message="Already exists")

    @app.get("/_test/crash")
    async def crash() -> None:
        raise RuntimeError("boom-secret-internal-detail")

    @app.get("/_test/items/{item_id}")
    async def item(item_id: int) -> dict[str, int]:
        return {"item_id": item_id}

    @app.get("/_test/echo-id")
    async def echo_id() -> dict[str, str | None]:
        await asyncio.sleep(0.01)  # yield to the loop so concurrent requests interleave
        return {"request_id": get_request_id()}

    @app.get("/_test/echo-id-sync")
    def echo_id_sync() -> dict[str, str | None]:  # sync endpoint => runs in a worker thread
        return {"request_id": get_request_id()}
