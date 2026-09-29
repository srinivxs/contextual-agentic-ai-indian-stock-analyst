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
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.chat.contract import (
    ABSTAIN_TEXT,
    AGREE_TEXT,
    FORECAST_TEXT,
    NO_EXPLANATION_TEXT,
    NO_SOURCES_TEXT,
    OUT_OF_SCOPE_TEXT,
    VALUATION_GAP_TEXT,
    VALUATION_NO_DATA_TEXT,
    WHICH_COMPANY_TEXT,
    WHICH_MEASURE_TEXT,
    Reply,
    Source,
    Turn,
)
from app.chat.graph import GraphChatEngine
from app.embeddings import vector_literal
from app.llm import ToolSpec
from app.memory.store import get_profile
from tests.fake_llm import FakeLlm
from tests.fakes import FakeEmbedder
from tests.integration.conftest import MakeUser

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
    session_factory: Factory,
    llm: FakeLlm,
    question: str,
    history: list[Turn] | None = None,
    user_id: UUID | None = None,
) -> Reply:
    engine = GraphChatEngine(
        session_factory=session_factory,
        embedder=FakeEmbedder(),
        llm=llm,
        today=lambda: date(2026, 9, 29),
    )
    return await engine.answer(question=question, history=history or [], user_id=user_id or uuid4())


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
        'report, reported as "Revenue from Operations"). [1] screener.in gives ₹985 crore for '
        'FY2024 (reported as "Sales"), against ₹1,000 crore in the annual report (reported as '
        '"Revenue from Operations"); the stored data does not establish that the two measure the '
        "same thing, so they are not treated as interchangeable. [2]"
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
            "TCS's net profit for FY2025 was ₹200 crore (consolidated; screener.in, reported "
            'as "Net Profit"). [1]',
            ("205", "200", "190"),
        ),
        (
            "What was HDFC Bank's net profit in FY2025?",
            "HDFC Bank's net profit for FY2025 was ₹300 crore (consolidated; screener.in, "
            'reported as "Net Profit"). [1]',
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
    assert 'screener.in gives ₹1,080 crore for FY2025 (reported as "Sales")' in reply.text
    assert 'screener.in gives ₹985 crore for FY2024 (reported as "Sales")' in reply.text
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
        "screener.in. Figures used: ₹1,080 crore (FY2025), ₹1,210 crore (FY2026). Calculation: "
        "(₹1,210 crore - ₹1,080 crore) / ₹1,080 crore x 100 = 12%; up ₹130 crore."
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

    # code finds no company here; the model names one the question does not contain
    reply = await ask(session_factory, llm, "What is the weather in Mumbai?")

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


# --- the owner's second review (2026-09-29) -------------------------------------------------------

ABOUT_TCS = [
    Turn(role="user", text="Calculate the net profit growth for TCS from FY2025 to FY2026.")
]


@pytest.mark.parametrize(
    ("question", "named"),
    [
        ("What is Infosys's FY2025 revenue?", "Infosys"),  # A: after a question about TCS
        ("What is ICICI Bank's net profit?", "ICICI Bank"),  # B
        ("What is Apple revenue?", "Apple"),
        ("Compare TCS with Infosys.", "Infosys"),
    ],
)
async def test_another_company_is_refused_before_retrieval_never_answered_with_ours(
    session_factory: Factory, admin_engine: AsyncEngine, question: str, named: str
) -> None:
    await seed(admin_engine)
    llm = FakeLlm()

    reply = await ask(session_factory, llm, question, ABOUT_TCS)

    assert llm.calls == []
    assert (reply.status, reply.sources, reply.table) == ("out_of_scope", (), None)
    assert reply.text == f"{OUT_OF_SCOPE_TEXT} I don't have grounded data for {named}."
    assert "₹" not in reply.text  # no figure of ours stands in for theirs


async def test_c_a_share_price_a_year_from_now_is_refused_by_code(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    llm = FakeLlm()
    reply = await ask(session_factory, llm, "What will TCS's share price be one year from now?")
    assert (reply.status, reply.text, llm.calls) == ("abstained", FORECAST_TEXT, [])


REMEMBER = (
    "Remember that I'm a conservative investor who prefers stable growth and avoids highly "
    "leveraged companies."
)


async def test_d_e_a_stated_profile_is_stored_and_read_back_by_code(
    session_factory: Factory, admin_engine: AsyncEngine, make_user: MakeUser
) -> None:
    await seed(admin_engine)
    user = await make_user()
    llm = FakeLlm()

    stored = await ask(session_factory, llm, REMEMBER, user_id=user)
    asked = await ask(
        session_factory,
        llm,
        "What information do you remember about my investment preferences?",
        user_id=user,
    )

    assert stored.status == "remembered"
    async with session_factory() as db:
        profile = {p.field: p.values for p in await get_profile(db, user)}
    assert profile == {
        "risk_preference": ("conservative",),
        "debt_preference": ("avoid_high_debt",),
        "investment_style": ("growth",),
        "other_preferences": ("stability",),
    }
    assert asked.status == "remembered"
    assert asked.text.startswith(
        "Here is what I remember about your investment preferences: Risk: Conservative"
    )
    assert "Debt: Avoid high debt" in asked.text
    assert llm.calls == []


async def test_asking_for_information_leaves_the_profile_unchanged(
    session_factory: Factory, admin_engine: AsyncEngine, make_user: MakeUser
) -> None:
    await seed(admin_engine)
    user = await make_user()
    await ask(session_factory, FakeLlm(), REMEMBER, user_id=user)

    await ask(
        session_factory, FakeLlm(), "I want to know whether TCS is undervalued.", user_id=user
    )

    async with session_factory() as db:
        style = [p.values for p in await get_profile(db, user) if p.field == "investment_style"]
    assert style == [("growth",)]  # not "value"


async def test_f_a_personal_question_is_answered_from_the_profile_and_the_match_verdicts(
    session_factory: Factory, admin_engine: AsyncEngine, make_user: MakeUser
) -> None:
    await seed(admin_engine)
    user = await make_user()
    await ask(session_factory, FakeLlm(), REMEMBER, user_id=user)

    def explains(system: str, user_text: str, tool: ToolSpec) -> dict[str, Any]:
        verdict = evidence_id(user_text, "TCS · Match for your profile")
        return {
            "outcome": "answer",
            "claims": [claim("TCS's verdict follows its figures.", verdict)],
        }

    llm = FakeLlm(explains)
    question = "Which of TCS, HDFC Bank, and Reliance fits my stated preferences?"
    reply = await ask(session_factory, llm, question, user_id=user)

    assert "- Risk: Conservative" in llm.calls[0][1]  # the profile, as context
    assert "[M1]" in llm.calls[0][1]  # the verdicts, computed by code
    assert reply.status in ("answered", "abstained")  # the checker decides; the route is shown


async def test_a_personal_question_with_no_profile_says_how_to_give_one(
    session_factory: Factory, admin_engine: AsyncEngine, make_user: MakeUser
) -> None:
    await seed(admin_engine)
    user = await make_user()
    llm = FakeLlm()
    question = "Which of TCS, HDFC Bank, and Reliance should I research further?"
    reply = await ask(session_factory, llm, question, user_id=user)
    assert reply.status == "abstained"
    assert reply.text.startswith("I don't have any investment preferences saved for you yet.")
    assert llm.calls == []


async def test_g_h_every_reported_figure_is_given_with_what_its_source_calls_it(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    llm = FakeLlm()

    canonical = await ask(session_factory, llm, "What was Reliance's FY2025 revenue?")
    different = await ask(
        session_factory, llm, "What are the different reported FY2025 Reliance revenue figures?"
    )

    assert llm.calls == []
    for reply in (canonical, different):
        assert reply.text.startswith(
            "Reliance's revenue from operations for FY2025 was ₹1,100 crore (consolidated; annual "
            'report, reported as "Revenue from Operations"). [1] screener.in gives ₹1,080 crore'
        )
        assert "not treated as interchangeable" in reply.text
        assert [s.source for s in reply.sources] == ["filing", "screener"]


async def test_i_a_passage_that_states_no_cause_does_not_explain_a_change(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    passage = "Reliance revenue change between FY2024 and FY2025 in a resilient year for the group."
    await seed(admin_engine, passages={"RELIANCE": passage})

    def paraphrases(system: str, user: str, tool: ToolSpec) -> dict[str, Any]:
        source = evidence_id(user, "resilient year")
        return {"outcome": "answer", "claims": [claim("The year was resilient.", source)]}

    reply = await ask(session_factory, FakeLlm(paraphrases), WHY)

    assert reply.status == "answered"
    assert reply.text.endswith(NO_EXPLANATION_TEXT)


async def test_j_news_sentiment_is_never_evidence_about_the_results(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    calls = 0

    def leaps_then_reports(system: str, user: str, tool: ToolSpec) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        mood = evidence_id(user, "News sentiment")
        text = (
            "TCS's news sentiment supports its latest performance."
            if calls == 1
            else "TCS's news sentiment is shown for the last year."
        )
        return {"outcome": "answer", "claims": [claim(text, mood)]}

    llm = FakeLlm(leaps_then_reports)
    question = (
        "Find a recent TCS news item and explain whether it supports or contradicts the latest "
        "financial performance."
    )
    reply = await ask(session_factory, llm, question)

    assert "sentiment_as_evidence" in llm.calls[1][1]
    assert reply.status == "answered"
    assert NO_EXPLANATION_TEXT not in reply.text  # "explain whether" is not a why question


async def test_k_a_calculation_shows_its_inputs_and_is_marked_computed(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)

    def calculates(system: str, user: str, tool: ToolSpec) -> dict[str, Any]:
        change = evidence_id(user, "Net profit change, FY2025 to FY2026")
        text = "TCS's net profit grew 2.5% from ₹200 crore in FY2025 to ₹205 crore in FY2026."
        return {"outcome": "answer", "claims": [claim(text, change)]}

    question = "Calculate TCS net profit growth from FY2025 to FY2026."
    reply = await ask(session_factory, FakeLlm(calculates), question)

    assert reply.status == "answered"
    [computed] = reply.sources
    assert computed.source == "derived"  # the page marks it "Computed"
    assert computed.quote is not None
    assert "Calculation: (₹205 crore - ₹200 crore) / ₹200 crore x 100 = 2.5%" in computed.quote


async def test_where_an_answer_came_from_is_its_own_stored_sources(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    earlier = (
        Source(1, "filing", "Annual report · Annual Report 2025 · p.127", f"{BSE}#page=127", "q"),
        Source(2, "screener", "screener.in · profit-loss · Sales · Mar 2025", None, None),
    )
    history = [
        Turn(role="user", text="What happened to Reliance's revenue in FY2025?"),
        Turn(role="assistant", text="It was reported ... [1][2]", sources=earlier),
    ]
    llm = FakeLlm()

    reply = await ask(session_factory, llm, "Where exactly did you get that information?", history)

    assert llm.calls == []
    assert reply.text == (
        "That answer rests on: Annual report · Annual Report 2025 · p.127 (official filing on "
        "BSE). [1] screener.in · profit-loss · Sales · Mar 2025 (screener.in's fundamentals "
        "table). [2]"
    )
    assert [s.label for s in reply.sources] == [source.label for source in earlier]


async def test_a_valuation_question_without_a_stored_price_says_what_is_missing(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    llm = FakeLlm()
    question = "I want to know whether TCS is undervalued. Give me a definitive answer."
    reply = await ask(session_factory, llm, question)
    assert (reply.status, reply.text, llm.calls) == ("abstained", VALUATION_NO_DATA_TEXT, [])


async def test_an_unknown_company_s_figure_is_asked_back_never_answered_with_ours(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    llm = FakeLlm()

    # lower case and on no list: code cannot tell it is a company, and after a question about
    # TCS it does not borrow TCS (it does not refer back): it asks which of ours is meant
    reply = await ask(session_factory, llm, "what is quuxcorp revenue", ABOUT_TCS)

    assert (reply.text, reply.sources, llm.calls) == (WHICH_COMPANY_TEXT, (), [])


async def test_an_unknown_company_code_misses_is_left_to_the_model_and_named_from_the_question(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    llm = FakeLlm([{"outcome": "out_of_scope", "claims": [], "other_company": "Quuxcorp"}])

    reply = await ask(session_factory, llm, "what does quuxcorp do", ABOUT_TCS)

    assert "TCS" not in llm.calls[0][1].split("Evidence:")[0].split("Question:")[1]
    assert (reply.status, reply.text) == (
        "out_of_scope",
        f"{OUT_OF_SCOPE_TEXT} I don't have grounded data for quuxcorp.",
    )


async def test_where_from_with_no_earlier_answer_says_so(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    reply = await ask(session_factory, FakeLlm(), "Where exactly did you get that information?")
    assert (reply.status, reply.text) == ("abstained", NO_SOURCES_TEXT)


async def test_a_question_about_disagreeing_sources_says_when_they_agree(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)

    def lists(system: str, user: str, tool: ToolSpec) -> dict[str, Any]:
        fy2026 = evidence_id(user, "TCS · Net profit · FY2026")
        return {"outcome": "answer", "claims": [claim("FY2026 net profit was ₹205 crore.", fy2026)]}

    question = "TCS net profit for FY2024 to FY2026: any inconsistencies between your sources?"
    reply = await ask(session_factory, FakeLlm(lists), question)

    assert reply.text.endswith(AGREE_TEXT)


async def test_a_valuation_question_gets_the_stored_price_to_earnings_and_what_is_missing(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO facts (stock_id, source, source_url, source_section, source_row, "
                "source_column, metric, period, period_end, basis, currency, unit, value) "
                "SELECT id, 'screener', :url, 'profit-loss', 'EPS in Rs', 'Mar 2026', "
                "'eps_basic', 'FY2026', DATE '2026-03-31', 'consolidated', 'INR', "
                "'INR_PER_SHARE', 10 FROM stocks WHERE symbol = 'TCS'"
            ),
            {"url": SCREENER.format("TCS")},
        )
        await connection.execute(
            text(
                "INSERT INTO prices (stock_id, trade_date, open, high, low, close, prev_close, "
                "volume) SELECT id, DATE '2026-09-28', 210, 210, 210, 210, 200, 1 FROM stocks "
                "WHERE symbol = 'TCS'"
            )
        )
    try:
        llm = FakeLlm()
        reply = await ask(session_factory, llm, "Is TCS undervalued?")
    finally:
        async with admin_engine.begin() as connection:
            await connection.execute(text("DELETE FROM prices"))

    assert llm.calls == []
    assert reply.status == "answered"
    assert reply.text.startswith(
        "TCS's price to earnings is 21: Share price ₹210 (28 Sep 2026) divided by basic EPS of "
        "₹10 for FY2026"
    )
    assert reply.text.endswith(VALUATION_GAP_TEXT)
    assert [s.source for s in reply.sources] == ["derived"]


# --- the owner's third review (2026-09-29) --------------------------------------------------------


async def test_a_bank_s_revenue_is_never_its_net_interest_income_and_a_gap_is_named(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)

    def compares(system: str, user: str, tool: ToolSpec) -> dict[str, Any]:
        hdfc = evidence_id(user, "HDFCBANK · Net profit · FY2026")
        return {
            "outcome": "answer",
            "claims": [claim("HDFC Bank's net profit was ₹310 crore.", hdfc)],
        }

    llm = FakeLlm(compares)
    question = (
        "Compare TCS, HDFC Bank, and Reliance on their latest revenue and net profit. Clearly "
        "state the period for each figure."
    )
    reply = await ask(session_factory, llm, question)

    assert "Net interest income" not in llm.calls[0][1]  # never offered as revenue
    assert reply.status == "answered"
    assert "HDFC Bank's revenue from operations: not available in the current data." in reply.text


async def test_a_bank_s_revenue_asked_on_its_own_is_not_available_by_code(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    llm = FakeLlm()
    reply = await ask(session_factory, llm, "What is HDFC Bank's revenue?")
    assert (reply.status, reply.text, llm.calls) == (
        "abstained",
        "HDFC Bank's revenue from operations: not available in the current data.",
        [],
    )


async def test_a_figure_of_no_named_company_is_asked_back_with_choices_that_answer(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    llm = FakeLlm()

    asked_back = await ask(session_factory, llm, "What is the latest revenue?")

    assert (asked_back.text, asked_back.sources, llm.calls) == (WHICH_COMPANY_TEXT, (), [])
    assert [(c.label, c.question) for c in asked_back.choices] == [
        ("TCS", "What is the latest revenue for TCS?"),
        ("HDFC Bank", "What is the latest revenue for HDFC Bank?"),
        ("Reliance", "What is the latest revenue for Reliance?"),
    ]
    chosen = await ask(session_factory, llm, asked_back.choices[2].question)
    assert chosen.text.startswith("Reliance's revenue from operations for FY2026 was ₹1,210 crore")
    assert llm.calls == []  # a stored figure: stated by code


RELIANCE_REVENUE = [
    Turn(role="user", text="What is the latest revenue for Reliance?"),
    Turn(role="assistant", text="Reliance's revenue from operations for FY2026 ... [1]"),
]


async def test_compare_it_keeps_the_earlier_stock_and_measure(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)

    def compares(system: str, user: str, tool: ToolSpec) -> dict[str, Any]:
        reliance = evidence_id(user, "RELIANCE · Revenue from operations · FY2026")
        text = "Reliance's revenue was ₹1,210 crore in FY2026."
        return {"outcome": "answer", "claims": [claim(text, reliance)]}

    llm = FakeLlm(compares)
    reply = await ask(session_factory, llm, "Now compare it with HDFC Bank.", RELIANCE_REVENUE)

    assert reply.status == "answered"
    assert reply.text.startswith("Reliance's revenue was ₹1,210 crore in FY2026. [1]")
    assert reply.text.endswith(
        "HDFC Bank's revenue from operations: not available in the current data."
    )


async def test_a_comparison_with_no_measure_asks_which_one_with_choices(
    session_factory: Factory, admin_engine: AsyncEngine
) -> None:
    await seed(admin_engine)
    history = [Turn(role="user", text="Tell me about Reliance.")]
    llm = FakeLlm()

    reply = await ask(session_factory, llm, "Now compare it with HDFC Bank.", history)

    assert (reply.text, llm.calls) == (WHICH_MEASURE_TEXT, [])
    assert [(c.label, c.question) for c in reply.choices] == [
        ("Revenue", "Compare Reliance with HDFC Bank on revenue."),
        ("Net profit", "Compare Reliance with HDFC Bank on net profit."),
        ("Both", "Compare Reliance with HDFC Bank on revenue and net profit."),
    ]
