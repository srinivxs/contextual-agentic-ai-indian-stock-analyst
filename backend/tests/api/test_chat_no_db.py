"""The chat endpoints must refuse an anonymous or forged request WITHOUT the database or the engine.

A request with no session cookie has nothing to look up, and a foreign `Origin` is decided from the
request alone, so neither may cost a connection, let alone an LLM call.
"""

from typing import NoReturn
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import Settings
from app.main import create_app
from tests.fake_chat import FakeChatEngine

MESSAGES = "/api/v1/chat/messages"
CONVERSATIONS = "/api/v1/chat/conversations"
QUESTION = {"question": "What was DemoCo's net profit?"}


class NoDatabase:
    def __call__(self) -> NoReturn:
        raise AssertionError("the database must not be touched")


@pytest.fixture
def engine() -> FakeChatEngine:
    return FakeChatEngine()


@pytest.fixture(autouse=True)
def forbid_database(app: FastAPI, engine: FakeChatEngine) -> None:
    app.state.session_factory = NoDatabase()
    app.state.chat_engine = engine


def assert_401(response: httpx.Response) -> None:
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers


def test_a_new_app_has_no_chat_engine_yet(settings: Settings) -> None:
    """Switched off by default: without an engine the api cannot call an LLM or spend."""
    assert create_app(settings).state.chat_engine is None


async def test_asking_needs_a_session(client: httpx.AsyncClient, engine: FakeChatEngine) -> None:
    assert_401(await client.post(MESSAGES, json=QUESTION))
    assert engine.calls == []


async def test_asking_with_a_bad_body_still_gets_401_first(client: httpx.AsyncClient) -> None:
    """Authentication comes before validation, so nothing is revealed to an anonymous caller."""
    assert_401(await client.post(MESSAGES, json={"question": ""}))


async def test_listing_conversations_needs_a_session(client: httpx.AsyncClient) -> None:
    assert_401(await client.get(CONVERSATIONS))


async def test_reading_a_conversation_needs_a_session(client: httpx.AsyncClient) -> None:
    assert_401(await client.get(f"{CONVERSATIONS}/{uuid4()}"))
    assert_401(await client.get(f"{CONVERSATIONS}/not-a-uuid"))


@pytest.mark.parametrize(
    "origin",
    ["https://evil.example", "http://localhost:9999", "http://localhost:8000.evil.example", "null"],
)
async def test_a_foreign_origin_is_refused_before_anything_else(
    client: httpx.AsyncClient, engine: FakeChatEngine, origin: str
) -> None:
    response = await client.post(MESSAGES, json=QUESTION, headers={"Origin": origin})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert response.headers["cache-control"] == "no-store"
    assert engine.calls == []


async def test_our_own_origin_or_no_origin_gets_as_far_as_the_session_check(
    client: httpx.AsyncClient,
) -> None:
    for headers in ({"Origin": "http://localhost:8000"}, {}):
        assert_401(await client.post(MESSAGES, json=QUESTION, headers=headers))


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", MESSAGES),
        ("PUT", MESSAGES),
        ("POST", CONVERSATIONS),
        ("DELETE", CONVERSATIONS),
    ],
)
async def test_only_the_intended_methods_exist(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    """A link or prefetch (GET) must never be able to ask the chat anything."""
    response = await client.request(method, path)

    assert response.status_code == 405
    assert response.headers["cache-control"] == "no-store"
