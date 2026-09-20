"""The P5 "done when", as one automated flow against the real database.

Log in with (fake) Google, follow a stock, "refresh" (a brand-new app with nothing in memory), find
it still followed, log out, log in again, and find it still followed. The only fake is Google.
"""

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from tests.google_fakes import FakeGoogle, mint_id_token
from tests.integration.conftest import DbConfig
from tests.integration.test_auth_login_db import (
    cookie_from,
    finish_login,
    logging_in,
    start_login,
)

pytestmark = pytest.mark.usefixtures("clean_auth_tables")

ORIGIN = {"Origin": "http://localhost:8000"}
FOLLOW = "/api/v1/stocks/TCS/follow"


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


async def log_in(
    client: httpx.AsyncClient, google: FakeGoogle, settings: Settings
) -> dict[str, str]:
    signed, attempt = await start_login(client, settings)
    google.id_token = mint_id_token(nonce=attempt.nonce)
    response = await finish_login(client, signed, attempt)
    session = cookie_from(response, "session")
    assert session, "a successful login must hand out a session cookie"
    return {"Cookie": f"session={session}"}


async def followed(client: httpx.AsyncClient, cookie: dict[str, str]) -> dict[str, bool]:
    response = await client.get("/api/v1/stocks", headers=cookie)
    assert response.status_code == 200
    return {i["symbol"]: i["followed"] for i in response.json()["items"]}


async def test_login_follow_refresh_logout_and_login_again(
    db_config: DbConfig, google: FakeGoogle, admin_engine: AsyncEngine
) -> None:
    settings = db_config.settings()

    async with logging_in(db_config, google) as (_, client):
        cookie = await log_in(client, google, settings)
        assert await followed(client, cookie) == {
            "RELIANCE": False,
            "TCS": False,
            "HDFCBANK": False,
        }
        assert (await client.put(FOLLOW, headers={**cookie, **ORIGIN})).status_code == 204
        assert (await followed(client, cookie))["TCS"] is True

    # A refresh: the whole application is rebuilt, so only the database can remember the follow.
    async with logging_in(db_config, google) as (_, client):
        assert await followed(client, cookie) == {"RELIANCE": False, "TCS": True, "HDFCBANK": False}

        assert (
            await client.post("/api/v1/auth/logout", headers={**cookie, **ORIGIN})
        ).status_code == 204
        assert (await client.get("/api/v1/stocks", headers=cookie)).status_code == 401
        assert (await client.put(FOLLOW, headers={**cookie, **ORIGIN})).status_code == 401

    # Follows belong to the person, not to the session: signing in again finds them.
    async with logging_in(db_config, google) as (_, client):
        new_cookie = await log_in(client, google, settings)
        assert new_cookie != cookie
        assert (await followed(client, new_cookie))["TCS"] is True

    async with admin_engine.connect() as connection:
        users = (await connection.execute(text("SELECT count(*) FROM users"))).scalar_one()
        follows = (await connection.execute(text("SELECT count(*) FROM user_follows"))).scalar_one()
    assert (users, follows) == (1, 1)
