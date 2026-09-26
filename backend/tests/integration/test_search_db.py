"""Search by meaning over HTTP, against the real database (P10b).

    GET /api/v1/search?q=...&symbol=TCS
      -> the question's fingerprint (one Bedrock call; a fake here)
      -> the closest chunk fingerprints (pgvector, exact)  -> the rules in app/retrieval.py
      -> the top passages: filing, page, a short excerpt, the official BSE link

The fake embedder is a bag of words, so "closest in meaning" here means "shares the most words":
enough to check the plumbing, the filters and the order. Real quality is judged by eye on the
real filings.
"""

import hashlib
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.embeddings import vector_literal
from tests.fakes import FakeEmbedder
from tests.helpers import running_app
from tests.integration.auth_helpers import open_session
from tests.integration.conftest import DbConfig, MakeUser

pytestmark = pytest.mark.usefixtures("clean_document_tables")

Factory = async_sessionmaker[AsyncSession]
SEARCH = "/api/v1/search"


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def bse(number: int) -> str:
    return f"https://www.bseindia.com/xml-data/corpfiling/AttachHis/{number:08d}-demo.pdf"


async def add_filing(
    engine: AsyncEngine,
    number: int,
    pages: dict[int, str],
    *,
    symbol: str = "TCS",
    status: str = "completed",
    model: str = FakeEmbedder.model,
) -> int:
    """A filing as P9 and P10a leave it: chunks per page, each with its fingerprint."""
    fake = FakeEmbedder()
    async with engine.begin() as connection:
        document_id: int = (
            await connection.execute(
                text(
                    "INSERT INTO documents (stock_id, title, sha256, size_bytes, blob_key, source, "
                    "source_url, status, kind, period) "
                    "SELECT id, :title, :sha, 10, 'documents/' || :sha || '.pdf', 'bse', :url, "
                    ":status, 'transcript', 'Jul 2026' FROM stocks WHERE symbol = :symbol "
                    "RETURNING id"
                ),
                {
                    "title": f"{symbol} earnings call transcript, Jul 2026",
                    "sha": f"{number:064x}",
                    "url": bse(number),
                    "status": status,
                    "symbol": symbol,
                },
            )
        ).scalar_one()
        for ordinal, (page, chunk) in enumerate(pages.items()):
            await connection.execute(
                text(
                    "INSERT INTO chunks (document_id, ordinal, page_number, text, content_hash) "
                    "VALUES (:document, :ordinal, :page, :text, :hash)"
                ),
                {
                    "document": document_id,
                    "ordinal": ordinal,
                    "page": page,
                    "text": chunk,
                    "hash": sha(chunk),
                },
            )
            embedding = await fake.embed(chunk)
            await connection.execute(
                text(
                    "INSERT INTO embeddings (model, content_hash, embedding, input_tokens) "
                    "VALUES (:model, :hash, CAST(:vector AS vector), :tokens) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "model": model,
                    "hash": sha(chunk),
                    "vector": vector_literal(embedding.vector),
                    "tokens": embedding.tokens,
                },
            )
    return document_id


async def sign_in(make_user: MakeUser, session_factory: Factory) -> tuple[UUID, dict[str, str]]:
    user_id = await make_user()
    token = await open_session(session_factory, user_id)
    return user_id, {"Cookie": f"session={token}"}


def switched_on(db_config: DbConfig) -> Settings:
    return db_config.settings(embeddings_enabled=True)


def with_fake(app: FastAPI, fake: FakeEmbedder | None = None) -> FakeEmbedder:
    """The api would build a Bedrock embedder when switched on; tests swap in the fake."""
    embedder = fake or FakeEmbedder()
    app.state.embedder = embedder
    return embedder


async def search(
    db_config: DbConfig,
    cookie: dict[str, str],
    params: dict[str, str],
    fake: FakeEmbedder | None = None,
) -> tuple[Any, FakeEmbedder]:
    async with running_app(switched_on(db_config)) as (app, client):
        embedder = with_fake(app, fake)
        response = await client.get(SEARCH, params=params, headers=cookie)
    return response, embedder


# --- finding ------------------------------------------------------------------------------------


async def test_the_passage_that_answers_comes_back_with_its_page_and_link(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    document = await add_filing(
        admin_engine,
        1,
        {
            3: "DemoCo dividend policy was unchanged this year.",
            7: "Employee attrition at DemoCo fell to twelve percent this quarter.",
        },
    )
    _, cookie = await sign_in(make_user, session_factory)

    response, embedder = await search(db_config, cookie, {"q": "employee attrition"})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert embedder.calls == ["employee attrition"]  # one fingerprint per search: the question's
    first = response.json()["items"][0]
    assert first == {
        "symbol": "TCS",
        "document_id": document,
        "title": "TCS earnings call transcript, Jul 2026",
        "kind": "transcript",
        "period": "Jul 2026",
        "page": 7,
        "excerpt": "Employee attrition at DemoCo fell to twelve percent this quarter.",
        "source_url": bse(1),
        "score": first["score"],
    }
    assert 0 < first["score"] <= 1.05


async def test_the_symbol_limits_the_search_to_that_stock(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await add_filing(admin_engine, 1, {1: "DemoCo revenue grew strongly."}, symbol="TCS")
    await add_filing(admin_engine, 2, {1: "DemoCo revenue grew slowly."}, symbol="RELIANCE")
    _, cookie = await sign_in(make_user, session_factory)

    everything, _ = await search(db_config, cookie, {"q": "revenue grew"})
    reliance, _ = await search(db_config, cookie, {"q": "revenue grew", "symbol": "RELIANCE"})

    assert {item["symbol"] for item in everything.json()["items"]} == {"TCS", "RELIANCE"}
    assert [item["symbol"] for item in reliance.json()["items"]] == ["RELIANCE"]


async def test_only_ingested_documents_and_this_models_fingerprints_are_searched(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await add_filing(admin_engine, 1, {1: "DemoCo revenue grew."}, status="processing")
    await add_filing(admin_engine, 2, {1: "DemoCo revenue grew again."}, model="some-older-model")
    _, cookie = await sign_in(make_user, session_factory)

    response, _ = await search(db_config, cookie, {"q": "revenue grew"})

    assert response.json() == {"items": []}


async def test_at_most_five_passages_and_two_per_filing(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    for number in range(1, 5):
        await add_filing(
            admin_engine,
            number,
            {page: f"DemoCo margins report {number} page {page}" for page in range(1, 5)},
        )
    _, cookie = await sign_in(make_user, session_factory)

    items = (await search(db_config, cookie, {"q": "margins report"}))[0].json()["items"]

    assert len(items) == 5
    per_filing: dict[int, int] = {}
    for item in items:
        per_filing[item["document_id"]] = per_filing.get(item["document_id"], 0) + 1
    assert max(per_filing.values()) <= 2


async def test_a_long_passage_is_shown_as_a_short_excerpt(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """The stored text is never handed out whole (the project notes: about 300 characters and a link)."""
    await add_filing(admin_engine, 1, {1: "DemoCo guidance " + "detail " * 200})
    _, cookie = await sign_in(make_user, session_factory)

    [item] = (await search(db_config, cookie, {"q": "guidance"}))[0].json()["items"]

    assert len(item["excerpt"]) <= 301
    assert item["excerpt"].endswith("…")


# --- refusing ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [{}, {"q": "ab"}, {"q": "x" * 301}, {"q": "   "}, {"q": "revenue", "symbol": "tcs"}],
    ids=["no-question", "too-short", "too-long", "blank", "bad-symbol"],
)
async def test_a_bad_question_is_422_and_costs_nothing(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory, params: dict[str, str]
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    response, embedder = await search(db_config, cookie, params)
    assert response.status_code == 422
    assert embedder.calls == []


async def test_an_unknown_stock_is_404_and_costs_nothing(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    response, embedder = await search(db_config, cookie, {"q": "revenue", "symbol": "INFY"})
    assert response.status_code == 404
    assert "INFY" not in response.text
    assert embedder.calls == []


async def test_with_embeddings_switched_off_search_says_so(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    _, cookie = await sign_in(make_user, session_factory)
    async with running_app(db_config.settings(embeddings_enabled=False)) as (app, client):
        assert app.state.embedder is None  # no Bedrock client exists at all
        response = await client.get(SEARCH, params={"q": "revenue"}, headers=cookie)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_when_bedrock_fails_search_is_unavailable_and_says_nothing_more(
    db_config: DbConfig, make_user: MakeUser, session_factory: Factory
) -> None:
    """Throttling or an expired pass: a plain 503, never the AWS error text."""
    _, cookie = await sign_in(make_user, session_factory)
    broken = FakeEmbedder(fail_on="", error=RuntimeError("ExpiredToken: secret details"))

    response, _ = await search(db_config, cookie, {"q": "revenue"}, broken)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "unavailable"
    assert "ExpiredToken" not in response.text
