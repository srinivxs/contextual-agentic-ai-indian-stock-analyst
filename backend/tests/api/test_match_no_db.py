"""GET /api/v1/match without the database: the session check, and the JSON shape.

The rules themselves are tested in tests/unit; here a hand-made StockMatch goes in and the exact
JSON the Match page reads comes out.
"""

from collections.abc import AsyncIterator
from datetime import date
from types import TracebackType
from typing import Any, NoReturn
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api import match as match_api
from app.auth.deps import current_user
from app.auth.sessions import CurrentUser
from app.insights import Citation
from app.matching.model import Reason, StockMatch

MATCH = "/api/v1/match"


class NoDatabase:
    def __call__(self) -> NoReturn:
        raise AssertionError("the database must not be touched")


class FakeDb:
    """An async session factory that opens a session that is never queried."""

    def __call__(self) -> "FakeDb":
        return self

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


@pytest.fixture
async def signed_in(app: FastAPI, client: httpx.AsyncClient) -> AsyncIterator[httpx.AsyncClient]:
    app.state.session_factory = FakeDb()
    app.dependency_overrides[current_user] = lambda: CurrentUser(id=uuid4(), email="a@example.test")
    yield client
    app.dependency_overrides.clear()


async def test_matching_needs_a_session(app: FastAPI, client: httpx.AsyncClient) -> None:
    app.state.session_factory = NoDatabase()
    response = await client.get(MATCH)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert response.headers["cache-control"] == "no-store"


async def test_only_get_exists(client: httpx.AsyncClient) -> None:
    assert (await client.post(MATCH)).status_code == 405


async def test_the_json_shape(
    signed_in: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    cite = Citation("filing", "Annual report · p.44", "https://www.bseindia.com/x.pdf#page=44", "q")
    reason = Reason("debt", "avoid_high_debt", True, "pass", "Debt to equity is 0.25.", (cite,))
    caution = Reason("sentiment", "conservative", False, "miss", "Negative news.", ())
    seen: dict[str, Any] = {}

    async def fake_load(db: object, user_id: object) -> tuple[list[Any], list[Any]]:
        return [object()], [object()]

    def fake_match_all(profile: list[Any], stocks: list[Any], *, today: date) -> list[StockMatch]:
        seen.update(profile=len(profile), stocks=len(stocks), today=today)
        return [StockMatch("DEMO", "DemoCo", "partial", (reason,), (caution,))]

    monkeypatch.setattr(match_api, "_load", fake_load)
    monkeypatch.setattr(match_api, "match_all", fake_match_all)

    response = await signed_in.get(MATCH)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert seen == {"profile": 1, "stocks": 1, "today": date.today()}
    assert response.json() == {
        "profile_empty": False,
        "stocks": [
            {
                "symbol": "DEMO",
                "name": "DemoCo",
                "status": "partial",
                "reasons": [
                    {
                        "criterion": "debt",
                        "preference": "avoid_high_debt",
                        "hard": True,
                        "outcome": "pass",
                        "text": "Debt to equity is 0.25.",
                        "citations": [
                            {
                                "source": "filing",
                                "label": "Annual report · p.44",
                                "url": "https://www.bseindia.com/x.pdf#page=44",
                                "quote": "q",
                            }
                        ],
                    }
                ],
                "cautions": [
                    {
                        "criterion": "sentiment",
                        "preference": "conservative",
                        "hard": False,
                        "outcome": "miss",
                        "text": "Negative news.",
                        "citations": [],
                    }
                ],
            }
        ],
        "disclaimer": (
            "Not investment advice. The rules compare stored figures and end-of-day share "
            "prices with your stated preferences."
        ),
    }


async def test_an_empty_profile_is_flagged(
    signed_in: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_load(db: object, user_id: object) -> tuple[list[Any], list[Any]]:
        return [], []

    monkeypatch.setattr(match_api, "_load", fake_load)
    monkeypatch.setattr(match_api, "match_all", lambda profile, stocks, *, today: [])
    body = (await signed_in.get(MATCH)).json()
    assert body["profile_empty"] is True
    assert body["stocks"] == []
