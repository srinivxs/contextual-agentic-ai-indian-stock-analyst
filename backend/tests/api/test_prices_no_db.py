"""GET /api/v1/stocks/{symbol}/prices without the database: the session check and the validation."""

from collections.abc import AsyncIterator
from typing import NoReturn
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.auth.deps import current_user
from app.auth.sessions import CurrentUser

PRICES = "/api/v1/stocks/TCS/prices"


class NoDatabase:
    def __call__(self) -> NoReturn:
        raise AssertionError("the database must not be touched")


@pytest.fixture
def no_database(app: FastAPI) -> None:
    app.state.session_factory = NoDatabase()


@pytest.fixture
async def signed_in(
    app: FastAPI, client: httpx.AsyncClient, no_database: None
) -> AsyncIterator[httpx.AsyncClient]:
    app.dependency_overrides[current_user] = lambda: CurrentUser(id=uuid4(), email="a@example.test")
    yield client
    app.dependency_overrides.clear()


@pytest.mark.usefixtures("no_database")
async def test_the_prices_need_a_session(client: httpx.AsyncClient) -> None:
    response = await client.get(PRICES)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("symbol", ["tcs", "TCS%20X", "A" * 21])
async def test_the_ticker_is_validated_like_the_other_stock_routes(
    signed_in: httpx.AsyncClient, symbol: str
) -> None:
    response = await signed_in.get(f"/api/v1/stocks/{symbol}/prices")
    assert response.status_code == 422


async def test_only_get_exists(signed_in: httpx.AsyncClient) -> None:
    assert (await signed_in.post(PRICES)).status_code == 405
