"""Plain helpers shared by tests (fixtures live in conftest.py)."""

import asyncio
import io
import json
from typing import Any

from fastapi import FastAPI

from app.core.config import Settings
from app.core.context import get_request_id
from app.core.errors import AppError


def build_settings(**overrides: Any) -> Settings:
    """Settings for tests. `_env_file=None` so a developer's real .env can never leak in."""
    values: dict[str, Any] = {"app_env": "test", "log_level": "INFO", **overrides}
    return Settings(_env_file=None, **values)


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
