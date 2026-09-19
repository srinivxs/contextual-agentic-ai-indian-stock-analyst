"""Shared fixtures.

Every test gets a fresh app built by the real ``create_app`` factory, talked to over an in-process
ASGI transport (no sockets).
"""

import io
import logging
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.main import create_app
from tests.helpers import add_test_routes, build_settings


@pytest.fixture
def settings() -> Settings:
    return build_settings()


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    application = create_app(settings)
    add_test_routes(application)
    return application


@pytest.fixture
def log_stream(app: FastAPI) -> Iterator[io.StringIO]:
    """Capture log output via the real JsonFormatter. Depends on `app` so logging is set up."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    yield stream
    root.removeHandler(handler)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    # raise_app_exceptions=False so an unhandled server error comes back as the 500 response
    # a real client would see, instead of re-raising inside the test.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http:
        yield http
