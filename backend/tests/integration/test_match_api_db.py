"""GET /api/v1/match against the real database (P14).

A remembered profile plus seeded synthetic facts gives a status per stock; an empty profile is
flagged; one user never sees another's profile.
"""

from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.memory.store import remember
from app.memory.vocabulary import Preference
from tests.helpers import running_app
from tests.integration.auth_helpers import open_session
from tests.integration.conftest import DbConfig, MakeUser

pytestmark = pytest.mark.usefixtures("clean_document_tables")

Factory = async_sessionmaker[AsyncSession]
BSE = "https://www.bseindia.com/xml-data/corpfiling/AttachHis/00000001-demo.pdf"
AVOID_DEBT = Preference("debt_preference", ("avoid_high_debt",), "I avoid high debt.")


async def sign_in(make_user: MakeUser, session_factory: Factory) -> tuple[UUID, dict[str, str]]:
    user_id = await make_user()
    token = await open_session(session_factory, user_id)
    return user_id, {"Cookie": f"session={token}"}


async def remember_for(session_factory: Factory, user_id: UUID, *prefs: Preference) -> None:
    async with session_factory() as db:
        await remember(db, user_id, list(prefs))
        await db.commit()


async def seed_debt(engine: AsyncEngine, borrowings: str, equity: str) -> None:
    """Synthetic FY2026 borrowings and equity for TCS, from a completed annual report."""
    async with engine.begin() as connection:
        document = (
            await connection.execute(
                text(
                    "INSERT INTO documents (stock_id, title, sha256, size_bytes, blob_key, source, "
                    "source_url, status, kind, period) SELECT id, 'DemoCo annual report', "
                    "repeat('a', 64), 10, 'documents/' || repeat('a', 64) || '.pdf', 'bse', :url, "
                    "'completed', 'annual_report', 'Annual Report 2026' "
                    "FROM stocks WHERE symbol = 'TCS' RETURNING id"
                ),
                {"url": BSE},
            )
        ).scalar_one()
        for metric, value in (
            ("total_borrowings", Decimal(borrowings)),
            ("total_equity", Decimal(equity)),
        ):
            await connection.execute(
                text(
                    "INSERT INTO facts (stock_id, source, document_id, page_number, quote, "
                    "metric, period, period_end, basis, currency, unit, value) "
                    "SELECT stock_id, 'filing', id, 44, :quote, :metric, 'FY2026', "
                    "DATE '2026-03-31', 'consolidated', 'INR', 'INR_CRORE', :value "
                    "FROM documents WHERE id = :document"
                ),
                {"document": document, "metric": metric, "value": value, "quote": f"{metric} q"},
            )


async def test_a_remembered_profile_gives_a_status_per_stock(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed_debt(admin_engine, "250", "1000")
    user_id, cookie = await sign_in(make_user, session_factory)
    await remember_for(session_factory, user_id, AVOID_DEBT)

    async with running_app(db_config.settings()) as (_, client):
        response = await client.get("/api/v1/match", headers=cookie)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["profile_empty"] is False
    assert body["disclaimer"].startswith("Not investment advice.")
    statuses = {s["symbol"]: s["status"] for s in body["stocks"]}
    assert statuses == {
        "RELIANCE": "not_enough_data",
        "TCS": "match",
        "HDFCBANK": "not_enough_data",
    }
    tcs = next(s for s in body["stocks"] if s["symbol"] == "TCS")
    debt = next(r for r in tcs["reasons"] if r["criterion"] == "debt")
    assert (debt["hard"], debt["outcome"]) == (True, "pass")
    assert "0.25" in debt["text"]
    assert {c["source"] for c in debt["citations"]} == {"filing"}
    assert all(c["url"].startswith(BSE) for c in debt["citations"])


async def test_a_hard_preference_that_is_broken_gives_no_match(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed_debt(admin_engine, "2500", "1000")
    user_id, cookie = await sign_in(make_user, session_factory)
    await remember_for(session_factory, user_id, AVOID_DEBT)

    async with running_app(db_config.settings()) as (_, client):
        body = (await client.get("/api/v1/match", headers=cookie)).json()

    tcs = next(s for s in body["stocks"] if s["symbol"] == "TCS")
    assert tcs["status"] == "no_match"
    assert any(r["outcome"] == "fail" for r in tcs["reasons"])


async def test_an_empty_profile_is_flagged_and_nothing_is_judged(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed_debt(admin_engine, "250", "1000")
    _, cookie = await sign_in(make_user, session_factory)

    async with running_app(db_config.settings()) as (_, client):
        body = (await client.get("/api/v1/match", headers=cookie)).json()

    assert body["profile_empty"] is True
    assert [s["status"] for s in body["stocks"]] == ["not_enough_data"] * 3
    assert all(s["reasons"] == [] for s in body["stocks"])


async def test_one_user_never_sees_another_users_profile(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed_debt(admin_engine, "250", "1000")
    owner, _ = await sign_in(make_user, session_factory)
    await remember_for(session_factory, owner, AVOID_DEBT)
    other = await make_user(sub="other-sub", email="other@example.test")
    other_cookie = {"Cookie": f"session={await open_session(session_factory, other)}"}

    async with running_app(db_config.settings()) as (_, client):
        body = (await client.get("/api/v1/match", headers=other_cookie)).json()

    assert body["profile_empty"] is True
    assert all(s["status"] == "not_enough_data" for s in body["stocks"])
