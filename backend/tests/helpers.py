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
# The origin the browser sees. Local, plain HTTP: session cookies are not `Secure` in this setup.
TEST_PUBLIC_BASE_URL = "http://localhost:8000"


def build_settings(**overrides: Any) -> Settings:
    """Settings for tests. `_env_file=None` so a developer's real .env can never leak in."""
    values: dict[str, Any] = {
        "app_env": "test",
        "log_level": "INFO",
        "database_url": TEST_DATABASE_URL,
        "public_base_url": TEST_PUBLIC_BASE_URL,
        **overrides,
    }
    return Settings(_env_file=None, **values)


def production_settings(**overrides: Any) -> Settings:
    """Settings that satisfy the production rules: HTTPS origin and `Secure` cookies."""
    return build_settings(
        app_env="production",
        public_base_url="https://app.example.test",
        cookie_secure=True,
        **overrides,
    )


def parse_set_cookie(header: str) -> tuple[str, str, dict[str, str]]:
    """Split one Set-Cookie header into (name, value, attributes).

    Attribute names are lower-cased; flags such as HttpOnly and Secure map to an empty string.
    """
    first, *rest = [part.strip() for part in header.split(";")]
    name, _, value = first.partition("=")
    attributes: dict[str, str] = {}
    for part in rest:
        key, _, attribute_value = part.partition("=")
        attributes[key.strip().lower()] = attribute_value.strip()
    return name, value, attributes


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
