"""The profile endpoints must refuse an anonymous or forged request WITHOUT the database.

A request with no session cookie has nothing to look up, and a foreign `Origin` is decided from the
request alone, so neither may cost a connection.
"""

from typing import NoReturn

import httpx
import pytest
from fastapi import FastAPI

PROFILE = "/api/v1/profile"
FIELD = "/api/v1/profile/risk_preference"


class NoDatabase:
    def __call__(self) -> NoReturn:
        raise AssertionError("the database must not be touched")


@pytest.fixture(autouse=True)
def forbid_database(app: FastAPI) -> None:
    app.state.session_factory = NoDatabase()


def assert_401(response: httpx.Response) -> None:
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers


async def test_reading_the_profile_needs_a_session(client: httpx.AsyncClient) -> None:
    assert_401(await client.get(PROFILE))


async def test_setting_a_field_needs_a_session(client: httpx.AsyncClient) -> None:
    assert_401(await client.put(FIELD, json={"values": ["conservative"]}))


async def test_setting_a_field_with_a_bad_body_still_gets_401_first(
    client: httpx.AsyncClient,
) -> None:
    assert_401(await client.put(FIELD, json={"values": []}))


async def test_forgetting_a_field_needs_a_session(client: httpx.AsyncClient) -> None:
    assert_401(await client.delete(FIELD))


async def test_forgetting_everything_needs_a_session(client: httpx.AsyncClient) -> None:
    assert_401(await client.delete(PROFILE))


@pytest.mark.parametrize(
    "origin",
    ["https://evil.example", "http://localhost:9999", "http://localhost:8000.evil.example", "null"],
)
async def test_a_foreign_origin_is_refused_before_the_session_on_put(
    client: httpx.AsyncClient, origin: str
) -> None:
    response = await client.put(
        FIELD, json={"values": ["conservative"]}, headers={"Origin": origin}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert response.headers["cache-control"] == "no-store"


async def test_a_foreign_origin_is_refused_before_the_session_on_delete(
    client: httpx.AsyncClient,
) -> None:
    response = await client.delete(FIELD, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    response = await client.delete(PROFILE, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


async def test_our_own_origin_or_no_origin_gets_as_far_as_the_session_check(
    client: httpx.AsyncClient,
) -> None:
    for headers in ({"Origin": "http://localhost:8000"}, {}):
        assert_401(await client.put(FIELD, json={"values": ["conservative"]}, headers=headers))


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", PROFILE), ("PATCH", FIELD), ("POST", FIELD)],
)
async def test_only_the_intended_methods_exist(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, path)
    assert response.status_code == 405
    assert response.headers["cache-control"] == "no-store"
