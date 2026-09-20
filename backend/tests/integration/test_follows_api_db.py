"""The stocks list and the follow endpoints over HTTP, against the real database."""

import asyncio
from datetime import datetime
from uuid import UUID

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tests.helpers import running_app
from tests.integration.auth_helpers import age_all_sessions, open_session
from tests.integration.conftest import DbConfig, MakeUser

Factory = async_sessionmaker[AsyncSession]
SEED_ORDER = ["RELIANCE", "TCS", "HDFCBANK"]
ORIGIN = {"Origin": "http://localhost:8000"}


def follow_url(symbol: str) -> str:
    return f"/api/v1/stocks/{symbol}/follow"


async def sign_in(
    make_user: MakeUser, session_factory: Factory, **user: str
) -> tuple[UUID, dict[str, str]]:
    user_id = await make_user(**user)
    token = await open_session(session_factory, user_id)
    return user_id, {"Cookie": f"session={token}"}


async def followed_symbols(engine: AsyncEngine, user_id: UUID) -> list[str]:
    async with engine.connect() as connection:
        result = await connection.execute(
            text(
                "SELECT s.symbol FROM user_follows f JOIN stocks s ON s.id = f.stock_id "
                "WHERE f.user_id = :user_id ORDER BY s.id"
            ),
            {"user_id": user_id},
        )
        return list(result.scalars())


async def total_follows(engine: AsyncEngine) -> int:
    async with engine.connect() as connection:
        count: int = (
            await connection.execute(text("SELECT count(*) FROM user_follows"))
        ).scalar_one()
        return count


async def listed(client: httpx.AsyncClient, cookie: dict[str, str]) -> dict[str, bool]:
    response = await client.get("/api/v1/stocks", headers=cookie)
    assert response.status_code == 200
    return {item["symbol"]: item["followed"] for item in response.json()["items"]}


# --- the list -------------------------------------------------------------------------------------


async def test_the_list_is_the_three_seeded_stocks_with_exactly_the_agreed_fields(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        response = await client.get("/api/v1/stocks", headers=cookie)
    async with admin_engine.connect() as connection:
        seeded = [
            tuple(row)
            for row in await connection.execute(
                text("SELECT symbol, name, bse_code, sector FROM stocks ORDER BY id")
            )
        ]

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"  # it carries the caller's own follows
    body = response.json()
    assert list(body) == ["items"]
    assert [
        tuple(i[k] for k in ("symbol", "name", "bse_code", "sector")) for i in body["items"]
    ] == seeded
    assert [i["symbol"] for i in body["items"]] == SEED_ORDER  # a stable order, the seed order
    for item in body["items"]:
        # Nothing internal (ids, our own is_financial classification) leaves the server.
        assert set(item) == {"symbol", "name", "bse_code", "sector", "followed"}


async def test_a_new_user_follows_nothing(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        assert await listed(client, cookie) == {"RELIANCE": False, "TCS": False, "HDFCBANK": False}


# --- following and unfollowing --------------------------------------------------------------------


async def test_following_is_a_204_with_no_body_and_shows_up_in_the_list(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    user_id, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        response = await client.put(follow_url("TCS"), headers={**cookie, **ORIGIN})
        after = await listed(client, cookie)

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["cache-control"] == "no-store"
    assert after == {"RELIANCE": False, "TCS": True, "HDFCBANK": False}
    assert await followed_symbols(admin_engine, user_id) == ["TCS"]


async def test_following_twice_is_idempotent(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    user_id, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        statuses = [
            (await client.put(follow_url("TCS"), headers={**cookie, **ORIGIN})).status_code
            for _ in range(3)
        ]

    assert statuses == [204, 204, 204]
    assert await followed_symbols(admin_engine, user_id) == ["TCS"]


async def test_repeating_a_follow_does_not_change_when_it_first_happened(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    user_id, cookie = await sign_in(make_user, session_factory)

    async def created_at() -> datetime:
        async with admin_engine.connect() as connection:
            result = await connection.execute(
                text("SELECT created_at FROM user_follows WHERE user_id = :u"), {"u": user_id}
            )
            found: datetime = result.scalar_one()
            return found

    async with running_app(db_config.settings()) as (_, client):
        await client.put(follow_url("TCS"), headers={**cookie, **ORIGIN})
        first = await created_at()
        await asyncio.sleep(0.05)
        await client.put(follow_url("TCS"), headers={**cookie, **ORIGIN})

    assert await created_at() == first


async def test_unfollowing_removes_the_follow_and_doing_it_again_is_fine(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    user_id, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        await client.put(follow_url("TCS"), headers={**cookie, **ORIGIN})
        statuses = [
            (await client.delete(follow_url("TCS"), headers={**cookie, **ORIGIN})).status_code
            for _ in range(2)
        ]
        after = await listed(client, cookie)

    assert statuses == [204, 204]
    assert after == {"RELIANCE": False, "TCS": False, "HDFCBANK": False}
    assert await followed_symbols(admin_engine, user_id) == []


async def test_unfollowing_something_never_followed_is_a_204(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        response = await client.delete(follow_url("HDFCBANK"), headers={**cookie, **ORIGIN})
    assert response.status_code == 204


async def test_all_three_can_be_followed_and_come_back_in_seed_order(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    user_id, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        for symbol in ("HDFCBANK", "RELIANCE", "TCS"):
            await client.put(follow_url(symbol), headers={**cookie, **ORIGIN})
        after = await listed(client, cookie)

    assert after == {"RELIANCE": True, "TCS": True, "HDFCBANK": True}
    assert await followed_symbols(admin_engine, user_id) == SEED_ORDER


# --- whose follows they are -----------------------------------------------------------------------


async def test_users_see_and_change_only_their_own_follows(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    ada_id, ada = await sign_in(make_user, session_factory, email="ada@example.test")
    bob_id, bob = await sign_in(make_user, session_factory, email="bob@example.test")
    async with running_app(db_config.settings()) as (_, client):
        await client.put(follow_url("TCS"), headers={**ada, **ORIGIN})
        bob_sees = await listed(client, bob)
        await client.put(follow_url("RELIANCE"), headers={**bob, **ORIGIN})
        await client.delete(follow_url("TCS"), headers={**bob, **ORIGIN})  # Bob never followed it

    assert bob_sees == {"RELIANCE": False, "TCS": False, "HDFCBANK": False}
    assert await followed_symbols(admin_engine, ada_id) == ["TCS"]  # untouched by Bob's DELETE
    assert await followed_symbols(admin_engine, bob_id) == ["RELIANCE"]


async def test_nothing_the_client_sends_can_name_another_user(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """The user is the session's user and nobody else, whatever the query or body says."""
    ada_id, ada = await sign_in(make_user, session_factory, email="ada@example.test")
    bob_id, _ = await sign_in(make_user, session_factory, email="bob@example.test")
    async with running_app(db_config.settings()) as (_, client):
        response = await client.put(
            follow_url("TCS"),
            params={"user_id": str(bob_id)},
            json={"user_id": str(bob_id)},
            headers={**ada, **ORIGIN},
        )

    assert response.status_code == 204
    assert await followed_symbols(admin_engine, ada_id) == ["TCS"]
    assert await followed_symbols(admin_engine, bob_id) == []


# --- symbols that are not stocks ------------------------------------------------------------------


@pytest.mark.parametrize("symbol", ["NOSUCH", "M%26M", "BAJAJ-AUTO"])
@pytest.mark.parametrize("method", ["PUT", "DELETE"])
async def test_a_well_formed_symbol_that_is_not_one_of_our_stocks_is_a_404(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    symbol: str,
    method: str,
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        response = await client.request(method, follow_url(symbol), headers={**cookie, **ORIGIN})
        # The route itself exists: a real symbol on the same route works. Otherwise a missing
        # route would also answer 404 and this test would pass for the wrong reason.
        real = await client.request(method, follow_url("TCS"), headers={**cookie, **ORIGIN})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert symbol not in response.text  # the caller's input is not echoed back
    assert real.status_code == 204
    assert await total_follows(admin_engine) == (1 if method == "PUT" else 0)  # only the real one


@pytest.mark.parametrize(
    "symbol", ["tcs", "T" * 21, "TCS%3BDROP%20TABLE%20users", "T%20S", "%E0%A4%85", "TCS%27--"]
)
async def test_a_badly_shaped_symbol_is_a_422_that_never_echoes_it(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    symbol: str,
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        response = await client.put(follow_url(symbol), headers={**cookie, **ORIGIN})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "DROP" not in response.text  # 422 details name the field, never repeat the input
    assert await total_follows(admin_engine) == 0


# --- who is allowed to ask ------------------------------------------------------------------------


@pytest.mark.parametrize("origin", ["https://evil.example", "null"])
async def test_a_foreign_origin_cannot_follow_or_unfollow_for_someone(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    origin: str,
) -> None:
    user_id, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        await client.put(follow_url("TCS"), headers={**cookie, **ORIGIN})
        forged_follow = await client.put(
            follow_url("HDFCBANK"), headers={**cookie, "Origin": origin}
        )
        forged_unfollow = await client.delete(
            follow_url("TCS"), headers={**cookie, "Origin": origin}
        )

    assert forged_follow.status_code == forged_unfollow.status_code == 403
    assert await followed_symbols(admin_engine, user_id) == ["TCS"]  # exactly as it was


async def test_a_non_browser_client_with_no_origin_is_allowed(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        response = await client.put(follow_url("TCS"), headers=cookie)
    assert response.status_code == 204


@pytest.mark.parametrize("kind", ["none", "garbage", "expired"])
async def test_without_a_valid_session_nothing_is_read_or_written(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    admin_engine: AsyncEngine,
    kind: str,
) -> None:
    user_id, cookie = await sign_in(make_user, session_factory)
    if kind == "expired":
        await age_all_sessions(admin_engine)
    headers = {"none": {}, "garbage": {"Cookie": "session=not-a-real-token"}, "expired": cookie}[
        kind
    ]
    async with running_app(db_config.settings()) as (_, client):
        listing = await client.get("/api/v1/stocks", headers=headers)
        follow = await client.put(follow_url("TCS"), headers={**headers, **ORIGIN})

    assert listing.status_code == follow.status_code == 401
    assert await followed_symbols(admin_engine, user_id) == []


# --- many at once ---------------------------------------------------------------------------------


async def test_twenty_simultaneous_follows_leave_exactly_one_row_and_no_errors(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    user_id, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        responses = await asyncio.gather(
            *(client.put(follow_url("TCS"), headers={**cookie, **ORIGIN}) for _ in range(20))
        )

    assert {r.status_code for r in responses} == {204}
    assert await followed_symbols(admin_engine, user_id) == ["TCS"]


async def test_simultaneous_follows_and_unfollows_never_error_and_end_consistent(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    user_id, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings()) as (_, client):
        responses = await asyncio.gather(
            *(
                client.request(
                    "PUT" if i % 2 else "DELETE", follow_url("TCS"), headers={**cookie, **ORIGIN}
                )
                for i in range(30)
            )
        )
        final = await listed(client, cookie)

    assert {r.status_code for r in responses} == {204}
    assert await followed_symbols(admin_engine, user_id) in ([], ["TCS"])  # one or the other
    assert final["TCS"] is ((await followed_symbols(admin_engine, user_id)) == ["TCS"])
