"""The owner's review of the chat (2026-09-29), end to end against the real database.

Six failures seen on real questions, each now a regression test with a fake LLM:

1. "What was Reliance's revenue in FY2024?" abstained although the figure was stored;
2. "Compare Reliance's FY2024 and FY2025 revenue." gave the annual report's figures in the text
   and screener.in's in the table;
3. "Why did Reliance's revenue change ...?" repeated the figures and explained nothing;
4. "What is Infosys's latest revenue?" must stay out of scope (and may name Infosys);
5. "What will TCS's share price be next year?" was called out of scope, though TCS is ours;
6. "What did HDFC Bank's management say about deposit growth in its latest earnings call?" came
   with a net profit table ("earnings call" was read as the profit metric).

All figures are synthetic, small round numbers on the seeded symbols, never real ones.
"""

import hashlib
import re
from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.chat.contract import (
    ABSTAIN_TEXT,
    FORECAST_TEXT,
    NO_EXPLANATION_TEXT,
    OUT_OF_SCOPE_TEXT,
    Reply,
    Turn,
)
from app.chat.graph import GraphChatEngine
from app.embeddings import vector_literal
from app.llm import ToolSpec
from tests.fake_llm import FakeLlm
from tests.fakes import FakeEmbedder

pytestmark = pytest.mark.usefixtures("migrated_db", "clean_document_tables")

Factory = async_sessionmaker[AsyncSession]
BSE = "https://www.bseindia.com/xml-data/corpfiling/AttachHis/0000000{}-demo.pdf"
SCREENER = "https://www.screener.in/company/{}/consolidated/"

# (symbol, metric, fiscal year, crore) from screener.in's table
SCREENER_FIGURES = [
    ("RELIANCE", "revenue_from_operations", 2026, 1210),
    ("RELIANCE", "revenue_from_operations", 2025, 1080),  # the annual report says 1,100
    ("RELIANCE", "revenue_from_operations", 2024, 985),  # the annual report says 1,000
    ("TCS", "net_profit", 2026, 205),
    ("TCS", "net_profit", 2025, 200),
    ("TCS", "net_profit", 2024, 190),
    ("HDFCBANK", "net_profit", 2026, 310),
    ("HDFCBANK", "net_profit", 2025, 300),
    ("HDFCBANK", "net_profit", 2024, 290),
]
REPORT_FIGURES = [("revenue_from_operations", 2025, 1100), ("revenue_from_operations", 2024, 1000)]

_DOCUMENT = text(
    "INSERT INTO documents (stock_id, title, sha256, size_bytes, blob_key, source, source_url, "
    "status, kind, period) SELECT id, :title, :sha, 10, 'documents/' || :sha || '.pdf', 'bse', "
    ":url, 'completed', :kind, :period FROM stocks WHERE symbol = :symbol RETURNING id"
)
_REPORT_FACT = text(
    "INSERT INTO facts (stock_id, source, document_id, page_number, quote, metric, period, "
    "period_end, basis, currency, unit, value) SELECT stock_id, 'filing', id, 127, :quote, "
    ":metric, :period, :end, 'consolidated', 'INR', 'INR_CRORE', :value FROM documents "
    "WHERE id = :id"
)
_SCREENER_FACT = text(
    "INSERT INTO facts (stock_id, source, source_url, source_section, source_row, source_column, "
    "metric, period, period_end, basis, currency, unit, value) SELECT id, 'screener', :url, "
    "'profit-loss', :row, :column, :metric, :period, :end, 'consolidated', 'INR', 'INR_CRORE', "
    ":value FROM stocks WHERE symbol = :symbol"
)


async def _document(connection: Any, symbol: str, kind: str, period: str, n: int) -> int:
    sha = f"{n:x}" * 64
    params = {"title": f"{symbol} {period}", "sha": sha, "url": BSE.format(n), "kind": kind}
    params |= {"period": period, "symbol": symbol}
    return int((await connection.execute(_DOCUMENT, params)).scalar_one())


async def _passage(connection: Any, document: int, ordinal: int, passage: str) -> None:
    digest = hashlib.sha256(passage.encode()).hexdigest()
    await connection.execute(
        text(
            "INSERT INTO chunks (document_id, ordinal, page_number, text, content_hash) "
            "VALUES (:id, :ordinal, 40, :text, :hash)"
        ),
        {"id": document, "ordinal": ordinal, "text": passage, "hash": digest},
    )
    fake = FakeEmbedder()
    vector = vector_literal((await fake.embed(passage)).vector)
    await connection.execute(
        text(
            "INSERT INTO embeddings (model, content_hash, embedding, input_tokens) "
            "VALUES (:model, :hash, CAST(:vector AS vector), 1) ON CONFLICT DO NOTHING"
        ),
        {"model": fake.model, "hash": digest, "vector": vector},
    )


async def seed(engine: AsyncEngine, *, passages: dict[str, str] | None = None) -> None:
    """Reliance's annual report (two years of revenue) and screener.in's figures; optionally a
    passage for a stock ("RELIANCE" in the annual report, "HDFCBANK" in an earnings call)."""
    async with engine.begin() as connection:
        report = await _document(connection, "RELIANCE", "annual_report", "Annual Report 2025", 1)
        call = await _document(connection, "HDFCBANK", "transcript", "Jul 2026", 2)
        for metric, year, value in REPORT_FIGURES:
            await connection.execute(
                _REPORT_FACT,
                {
                    "id": report,
                    "quote": f"Revenue from Operations {value}",
                    "metric": metric,
                    "period": f"FY{year}",
                    "end": date(year, 3, 31),
                    "value": value,
                },
            )
        for symbol, metric, year, value in SCREENER_FIGURES:
            await connection.execute(
                _SCREENER_FACT,
                {
                    "url": SCREENER.format(symbol),
                    "row": "Sales" if metric.startswith("revenue") else "Net Profit",
                    "column": f"Mar {year}",
                    "metric": metric,
                    "period": f"FY{year}",
                    "end": date(year, 3, 31),
                    "value": value,
                    "symbol": symbol,
                },
            )
        documents = {"RELIANCE": report, "HDFCBANK": call}
        for ordinal, (symbol, passage) in enumerate((passages or {}).items()):
            await _passage(connection, documents[symbol], ordinal, passage)


def evidence_id(user: str, *needles: str) -> str:
    """The ID of the evidence line containing every needle, read from the prompt."""
    for line in user.splitlines():
        match = re.match(r"\[([FDMNE]\d+)\]", line)
        if match and all(needle in line for needle in needles):
            return match.group(1)
    raise AssertionError(f"no evidence line with {needles}")


def claim(text: str, *citations: str) -> dict[str, Any]:
    return {"text": text, "citations": list(citations)}


async def ask(
    session_factory: Factory, llm: FakeLlm, question: str, history: list[Turn] | None = None
) -> Reply:
    engine = GraphChatEngine(
        session_factory=session_factory,
        embedder=FakeEmbedder(),
        llm=llm,
        today=lambda: date(2026, 9, 29),
    )
    return await engine.answer(question=question, history=history or [], user_id=uuid4())


# --- 1. a stored figure is answered by code -------------------------------------------------------


async def test_a_year_s_revenue_is_stated_by_code_with_its_source_and_the_other_source_named(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    llm = FakeLlm()

    reply = await ask(session_factory, llm, "What was Reliance's revenue in FY2024?")

    assert llm.calls == []  # no model: the figure is a stored row
    assert (reply.status, reply.model) == ("answered", None)
    assert reply.text == (
        "Reliance's revenue from operations for FY2024 was ₹1,000 crore (consolidated; annual "
        "report). [1] screener.in gives ₹985 crore for FY2024; sources can count revenue from "
        "operations differently. [2]"
    )
    assert [(s.source, s.label) for s in reply.sources] == [
        ("filing", "Annual report · Annual Report 2025 · p.127"),
        ("screener", "screener.in · profit-loss · Sales · Mar 2024"),
    ]
    assert reply.table is None  # screener.in's table would show ₹985 crore beside ₹1,000 crore


@pytest.mark.parametrize(
    ("question", "answer", "rows"),
    [
        (
            "What was TCS's net profit in FY2025?",
            "TCS's net profit for FY2025 was ₹200 crore (consolidated; screener.in). [1]",
            ("205", "200", "190"),
        ),
        (
            "What was HDFC Bank's net profit in FY2025?",
            "HDFC Bank's net profit for FY2025 was ₹300 crore (consolidated; screener.in). [1]",
            ("310", "300", "290"),
        ),
    ],
)
async def test_a_year_s_net_profit_is_stated_by_code_beside_a_table_that_agrees(
    session_factory: Factory,
    admin_engine: AsyncEngine,
    question: str,
    answer: str,
    rows: tuple[str, ...],
) -> None:
    await seed(admin_engine)
    llm = FakeLlm()

    reply = await ask(session_factory, llm, question)

    assert llm.calls == []
    assert reply.text == answer
    assert reply.table is not None
    assert tuple(row[1] for row in reply.table.rows) == rows


# --- 2. one figure per thing in one answer --------------------------------------------------------


async def test_a_comparison_takes_both_years_and_their_change_from_one_source(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)

    def compares(system: str, user: str, tool: ToolSpec) -> dict[str, Any]:
        fy2025 = evidence_id(user, "Revenue from operations · FY2025", "Annual report")
        fy2024 = evidence_id(user, "Revenue from operations · FY2024", "Annual report")
        change = evidence_id(user, "change, FY2024 to FY2025")
        return {
            "outcome": "answer",
            "claims": [
                claim("Reliance's FY2025 revenue was ₹1,100 crore.", fy2025),
                claim("Reliance's FY2024 revenue was ₹1,000 crore.", fy2024),
                claim("Revenue grew 10% from FY2024 to FY2025.", change),
            ],
        }

    llm = FakeLlm(compares)
    reply = await ask(session_factory, llm, "Compare Reliance's FY2024 and FY2025 revenue.")

    assert reply.status == "answered"
    # the change is computed from the very figures the answer gives, and says so
    assert "Figures used: ₹1,000 crore (FY2024), ₹1,100 crore (FY2025)." in llm.calls[0][1]
    # screener.in's differing figures are named with their source, never passed off as the same
    assert "screener.in gives ₹1,080 crore for FY2025" in reply.text
    assert "screener.in gives ₹985 crore for FY2024" in reply.text
    assert reply.table is None  # its figures would contradict the text


async def test_an_answer_resting_on_two_figures_for_one_year_is_refused(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    """The first real failure: one source's FY2025 figure beside a change computed from
    another's. The annual reports lack FY2026, so all three years come from screener.in."""
    await seed(admin_engine)

    def mixes(system: str, user: str, tool: ToolSpec) -> dict[str, Any]:
        report = evidence_id(user, "FY2025", "Annual report")
        change = evidence_id(user, "change, FY2025 to FY2026")
        claims = [claim("FY2025 revenue was ₹1,100 crore.", report), claim("It grew 12%.", change)]
        return {"outcome": "answer", "claims": claims}

    llm = FakeLlm(mixes)
    question = "How has Reliance's revenue changed over the last three years?"
    reply = await ask(session_factory, llm, question)

    assert (reply.status, reply.text) == ("abstained", ABSTAIN_TEXT)
    assert len(llm.calls) == 2
    assert "conflicting_figures" in llm.calls[1][1]


async def test_a_trend_from_one_source_comes_with_the_table_of_that_source(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)

    def trend(system: str, user: str, tool: ToolSpec) -> dict[str, Any]:
        latest = evidence_id(user, "change, FY2025 to FY2026")
        earlier = evidence_id(user, "change, FY2024 to FY2025")
        claims = [
            claim("Revenue grew 12% from FY2025 to FY2026.", latest),
            claim("Revenue grew 9.6% from FY2024 to FY2025.", earlier),
        ]
        return {"outcome": "answer", "claims": claims}

    question = "How has Reliance's revenue changed over the last three years?"
    reply = await ask(session_factory, FakeLlm(trend), question)

    assert reply.status == "answered"
    assert reply.table is not None
    assert tuple(row[1] for row in reply.table.rows) == ("1,210", "1,080", "985")
    assert reply.sources[0].quote == (
        "Change in revenue from operations from FY2025 to FY2026, consolidated figures from "
        "screener.in. Figures used: ₹1,080 crore (FY2025), ₹1,210 crore (FY2026)."
    )


# --- 3. why: only causes a filing states ----------------------------------------------------------

WHY = "Why did Reliance's revenue change between FY2024 and FY2025?"


async def test_a_why_answer_gives_the_cause_a_filing_states(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    passage = "Reliance revenue change between FY2024 and FY2025 came from higher retail volumes."
    await seed(admin_engine, passages={"RELIANCE": passage})

    def explains(system: str, user: str, tool: ToolSpec) -> dict[str, Any]:
        change = evidence_id(user, "change, FY2024 to FY2025")
        source = evidence_id(user, "higher retail volumes")
        text = "Revenue grew 10% from FY2024 to FY2025, driven by higher retail volumes."
        return {"outcome": "answer", "claims": [claim(text, change, source)]}

    reply = await ask(session_factory, FakeLlm(explains), WHY)

    assert reply.status == "answered"
    assert NO_EXPLANATION_TEXT not in reply.text
    assert "driven by higher retail volumes" in reply.text


async def test_a_why_answer_with_no_documented_cause_says_so(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    calls = 0

    def guesses_then_gives_figures(system: str, user: str, tool: ToolSpec) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        change = evidence_id(user, "change, FY2024 to FY2025")
        if calls == 1:  # a cause the figures cannot show
            return {
                "outcome": "answer",
                "claims": [claim("Revenue grew 10% due to demand.", change)],
            }
        return {"outcome": "answer", "claims": [claim("Revenue grew 10% from FY2024.", change)]}

    llm = FakeLlm(guesses_then_gives_figures)
    reply = await ask(session_factory, llm, WHY)

    assert "cause_without_source" in llm.calls[1][1]
    assert reply.status == "answered"
    assert reply.text.endswith(NO_EXPLANATION_TEXT)


# --- 4 and 5. out of scope only for other companies -----------------------------------------------


async def test_another_company_is_out_of_scope_and_named_as_the_question_writes_it(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    llm = FakeLlm([{"outcome": "out_of_scope", "claims": [], "other_company": "infosys"}])

    reply = await ask(session_factory, llm, "What is Infosys's latest revenue?")

    assert (reply.status, reply.text) == (
        "out_of_scope",
        "I currently have research data only for Reliance, TCS and HDFC Bank. I don't have "
        "grounded data for Infosys.",
    )


async def test_a_company_the_question_does_not_name_is_never_repeated(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    llm = FakeLlm([{"outcome": "out_of_scope", "claims": [], "other_company": "Wipro <b>"}])

    reply = await ask(session_factory, llm, "What is Infosys's latest revenue?")

    assert (reply.status, reply.text) == ("out_of_scope", OUT_OF_SCOPE_TEXT)


async def test_a_follow_up_about_another_company_stays_out_of_scope(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    llm = FakeLlm([{"outcome": "out_of_scope", "claims": [], "other_company": "Infosys"}])
    history = [Turn(role="user", text="What was TCS's net profit in FY2025?")]

    reply = await ask(session_factory, llm, "And what about Infosys?", history)

    assert reply.status == "out_of_scope"
    assert reply.text.endswith("I don't have grounded data for Infosys.")


async def test_a_common_word_is_never_taken_for_a_company_name(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    llm = FakeLlm([{"outcome": "out_of_scope", "claims": [], "other_company": "the"}])

    reply = await ask(session_factory, llm, "What is the weather in Mumbai?")

    assert (reply.status, reply.text) == ("out_of_scope", OUT_OF_SCOPE_TEXT)


async def test_a_future_share_price_of_one_of_our_stocks_is_refused_by_code(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    llm = FakeLlm()

    reply = await ask(session_factory, llm, "What will TCS's share price be next year?")

    assert (reply.status, reply.text, reply.model) == ("abstained", FORECAST_TEXT, None)
    assert llm.calls == []


async def test_out_of_scope_from_the_model_for_one_of_our_stocks_is_not_in_the_data(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    llm = FakeLlm([{"outcome": "out_of_scope", "claims": []}])

    reply = await ask(session_factory, llm, "What will TCS's revenue be in FY2030?")

    assert (reply.status, reply.text) == ("abstained", ABSTAIN_TEXT)


# --- 6. answer what was asked ---------------------------------------------------------------------


async def test_an_earnings_call_question_gets_no_net_profit_table(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    passage = "HDFC Bank management said deposit growth in the latest earnings call held up well."
    await seed(admin_engine, passages={"HDFCBANK": passage})

    def quotes(system: str, user: str, tool: ToolSpec) -> dict[str, Any]:
        source = evidence_id(user, "deposit growth")
        return {"outcome": "answer", "claims": [claim("Management said deposits held up.", source)]}

    question = (
        "What did HDFC Bank's management say about deposit growth in its latest earnings call?"
    )
    reply = await ask(session_factory, FakeLlm(quotes), question)

    assert reply.status == "answered"
    assert reply.table is None
