"""The profile's HTTP api against the real database (P13).

GET    /api/v1/profile
PUT    /api/v1/profile/{field}
DELETE /api/v1/profile/{field}
DELETE /api/v1/profile
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.memory.store import remember
from app.memory.vocabulary import Preference
from tests.helpers import running_app
from tests.integration.auth_helpers import open_session
from tests.integration.conftest import DbConfig, MakeUser

pytestmark = pytest.mark.usefixtures("migrated_db")

Factory = async_sessionmaker[AsyncSession]
ORIGIN = {"Origin": "http://localhost:8000"}
PROFILE = "/api/v1/profile"


async def sign_in(make_user: MakeUser, session_factory: Factory) -> tuple[UUID, dict[str, str]]:
    user_id = await make_user()
    token = await open_session(session_factory, user_id)
    return user_id, {"Cookie": f"session={token}", **ORIGIN}


@asynccontextmanager
async def profile_app(db_config: DbConfig, **overrides: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with running_app(db_config.settings(**overrides)) as (_, client):
        yield client


async def set_field(
    client: httpx.AsyncClient, headers: dict[str, str], field: str, values: list[str]
) -> httpx.Response:
    return await client.put(f"{PROFILE}/{field}", json={"values": values}, headers=headers)


async def test_a_new_user_has_an_empty_profile_and_the_full_choice_list(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    async with profile_app(db_config) as client:
        response = await client.get(PROFILE, headers=headers)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["fields"] == []
    assert [choice["field"] for choice in body["choices"]] == [
        "risk_preference",
        "debt_preference",
        "investment_style",
        "other_preferences",
    ]
    risk = body["choices"][0]
    assert risk["single"] is True
    assert risk["options"] == [
        {"value": "conservative", "label": "Conservative"},
        {"value": "moderate", "label": "Moderate"},
        {"value": "aggressive", "label": "Aggressive"},
    ]
    style = body["choices"][2]
    assert style["single"] is False


async def test_a_remembered_field_from_chat_is_returned(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    user_id, headers = await sign_in(make_user, session_factory)
    async with session_factory() as db:
        await remember(
            db,
            user_id,
            [Preference(field="risk_preference", values=("conservative",), quote="I hate risk.")],
        )
        await db.commit()

    async with profile_app(db_config) as client:
        response = await client.get(PROFILE, headers=headers)

    assert response.status_code == 200
    field = response.json()["fields"][0]
    assert field["field"] == "risk_preference"
    assert field["values"] == ["conservative"]
    assert field["labels"] == ["Conservative"]
    assert field["quote"] == "I hate risk."
    assert field["source"] == "chat"
    assert "T" in field["updated_at"]


async def test_setting_a_field_stores_it_with_source_edited(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    async with profile_app(db_config) as client:
        response = await client.put(
            f"{PROFILE}/investment_style", json={"values": ["income", "growth"]}, headers=headers
        )
        listed = await client.get(PROFILE, headers=headers)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body == {
        "field": "investment_style",
        "values": ["income", "growth"],
        "labels": ["Dividends / income", "Growth"],
        "quote": "Dividends / income, Growth",
        "source": "edited",
        "updated_at": body["updated_at"],
    }
    assert listed.json()["fields"] == [body]


async def test_setting_a_field_again_replaces_it(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    async with profile_app(db_config) as client:
        await set_field(client, headers, "risk_preference", ["moderate"])
        second = await set_field(client, headers, "risk_preference", ["aggressive"])
        listed = await client.get(PROFILE, headers=headers)

    assert second.json()["values"] == ["aggressive"]
    assert len(listed.json()["fields"]) == 1


@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("net_worth", ["conservative"]),  # unknown field
        ("risk_preference", ["reckless"]),  # not in this field's choices
        ("risk_preference", ["conservative", "aggressive"]),  # single-valued
        ("investment_style", ["income", "debt_ok"]),  # a tag from the wrong field
    ],
    ids=["unknown-field", "unknown-value", "too-many-for-single-valued", "wrong-fields-tag"],
)
async def test_a_malformed_field_or_values_is_refused(
    db_config: DbConfig,
    make_user: MakeUser,
    session_factory: Factory,
    field: str,
    values: list[str],
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    async with profile_app(db_config) as client:
        response = await set_field(client, headers, field, values)
        listed = await client.get(PROFILE, headers=headers)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert "input" not in str(response.json()["error"].get("details", ""))
    assert listed.json()["fields"] == []


async def test_forgetting_one_field_is_idempotent_and_keeps_the_others(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    async with profile_app(db_config) as client:
        await set_field(client, headers, "risk_preference", ["moderate"])
        await set_field(client, headers, "debt_preference", ["debt_ok"])

        first = await client.delete(f"{PROFILE}/risk_preference", headers=headers)
        second = await client.delete(f"{PROFILE}/risk_preference", headers=headers)
        listed = await client.get(PROFILE, headers=headers)

    assert (first.status_code, second.status_code) == (204, 204)
    assert first.headers["cache-control"] == "no-store"
    assert [f["field"] for f in listed.json()["fields"]] == ["debt_preference"]


async def test_forgetting_an_unknown_field_is_a_validation_error_not_a_404(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    async with profile_app(db_config) as client:
        response = await client.delete(f"{PROFILE}/net_worth", headers=headers)
    assert response.status_code == 422


async def test_forgetting_everything_is_idempotent(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, headers = await sign_in(make_user, session_factory)
    async with profile_app(db_config) as client:
        await set_field(client, headers, "risk_preference", ["moderate"])
        await set_field(client, headers, "debt_preference", ["debt_ok"])

        first = await client.delete(PROFILE, headers=headers)
        second = await client.delete(PROFILE, headers=headers)
        listed = await client.get(PROFILE, headers=headers)

    assert (first.status_code, second.status_code) == (204, 204)
    assert listed.json()["fields"] == []


async def test_one_user_never_sees_or_changes_another_users_profile(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, mine = await sign_in(make_user, session_factory)
    theirs_id, theirs = await sign_in(make_user, session_factory)
    async with session_factory() as db:
        await remember(
            db, theirs_id, [Preference(field="risk_preference", values=("moderate",), quote="Ok.")]
        )
        await db.commit()

    async with profile_app(db_config) as client:
        await set_field(client, mine, "debt_preference", ["debt_ok"])
        await client.delete(f"{PROFILE}/risk_preference", headers=mine)
        await client.delete(PROFILE, headers=mine)
        mine_listed = await client.get(PROFILE, headers=mine)
        theirs_listed = await client.get(PROFILE, headers=theirs)

    assert mine_listed.json()["fields"] == []  # forget_all cleared my own, harmless deletes too
    assert [f["field"] for f in theirs_listed.json()["fields"]] == ["risk_preference"]
