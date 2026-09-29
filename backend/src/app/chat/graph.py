"""The grounded chat as a small, explicit LangGraph workflow (P12, ADR 003).

    analyze ─> update_memory ─┬─> retrieve ─> grade ─┬─> generate ─> validate ─┬─> respond
                              │                      │                         ├─> generate (retry)
                              │                      ├─> lookup ─> respond     ├─> abstain
                              │                      └─> abstain <─────────────┴─> out_of_scope
                              ├─> forecast
                              └─> remembered

- analyze (code): which stocks, metrics, periods (app/chat/understand.py). No LLM: rule 8.
- update_memory (code, P13): the preferences the user's own message states (app/memory/extract.py)
  are saved, and the profile is loaded. It reads state["question"] and nothing else: never the
  history, the evidence or a reply, so no document can write memory (ADR 022). A message that
  only states preferences ends at remembered: "Noted. I'll remember: ...", with no LLM call.
- forecast (code): a question about a future share price, for a stock named, is refused with
  FORECAST_TEXT and no LLM call: end-of-day closes cannot predict a price.
- retrieve (code): each asked measure's figures from one source and the changes between them
  (app/chat/evidence.py's measures_for), debt to equity and the latest dividend (app/insights.py),
  the passages closest in meaning (app/retrieval.py) and, for news questions, the events and the
  rolling news sentiment, and for "Match me" questions the verdicts of app/matching/rules.py,
  numbered F#, D#, M#, N#, E# (app/chat/evidence.py).
- grade (code): a question that only asks for stored figures, all present, goes to lookup
  (app/chat/lookup.py: code states them, no LLM call); no evidence at all means "I don't have
  that in the data", without an LLM call.
- generate (LLM): one call, a forced tool filling a fixed form (app/chat/prompts.py).
- validate (code): every claim cites real evidence and every number is in what it cites
  (app/chat/answer_check.py). A failure gets one retry, told what failed; a second failure abstains.
  The model's "out of scope" stands only when the question names none of our stocks; for a named
  stock it becomes "I don't have that in the data" (the owner's review, 2026-09-29).
- respond (code): another source's differing figure is disclosed with its own source
  (app/chat/render.py's disclosures), then the [n] markers and the numbered sources; a "why"
  answer citing no filing passage or event says the data cannot establish why; and for a
  question about one stock's profit or revenue a year-by-year table built from stored screener.in
  figures (app/chat/tables.py), only when it agrees with the answer's own figures.

The LLM decides nothing about control flow: it only writes the answer's sentences. Every edge is
plain code, so the grounding path cannot be skipped, and each node is tested on its own.
"""

import logging
import re
from collections.abc import Callable
from datetime import date
from typing import Any, Literal, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.chat.answer_check import Claim, Problem, check_answer
from app.chat.contract import (
    ABSTAIN_TEXT,
    FORECAST_TEXT,
    NO_EXPLANATION_TEXT,
    DataTable,
    Reply,
    Turn,
    out_of_scope_text,
)
from app.chat.evidence import EvidenceItem, build_evidence, evidence_block, measures_for
from app.chat.lookup import lookup_claims
from app.chat.prompts import SYSTEM_PROMPT, Outcome, answer_tool, parse_answer, user_message
from app.chat.render import disclosures, render
from app.chat.tables import SERIES_METRICS, build_table, fits_answer
from app.chat.understand import Question, symbols_in, understand
from app.derived import Sentiment, rolling_sentiment
from app.embeddings import Embedder
from app.insights import DerivedView, KeyFact, StoredEvent, derived_views, key_facts
from app.insights_store import StockRows, load_stock
from app.llm import LlmError, StructuredLlm
from app.matching.rules import match_all
from app.memory.extract import describe, extract_preferences, preferences_text, profile_summary
from app.memory.store import get_profile, remember
from app.memory.vocabulary import Preference, StoredPreference
from app.prices.derived import PriceSnapshot, snapshot
from app.retrieval import Result, search
from app.series import SeriesPoint, load_series

logger = logging.getLogger("app.chat")

# A sentence opening with one of these asks for something, even without a "?" (P13).
_ASKS = re.compile(
    r"(?:^|[.!;\n]\s*)(?:what|which|how|why|when|who|is|are|does|do|can|should|tell|show|"
    r"compare|explain|list|give|find)\b",
    re.IGNORECASE,
)

MAX_ATTEMPTS = 2  # the first answer and one retry
MIN_SIMILARITY = 0.35  # a passage less similar than this to the question is not evidence
PASSAGES = 8
OTHER_DERIVED = ("debt_to_equity", "latest_dividend")  # growth comes from measures_for's changes
_COMPANY_NAME = re.compile(r"[A-Za-z][A-Za-z0-9 .&'-]{0,59}")
_NOT_A_NAME = {"a", "an", "the", "what", "which", "who", "how", "why", "is", "was", "it", "its"}

Verdict = Literal["respond", "retry", "abstain", "out_of_scope"]


def _named_in(question: str, company: str | None) -> str | None:
    """The other company as the question itself writes it, or None: the model's reading is
    shown only when it is a plain name found word for word in the user's question."""
    name = (company or "").strip()
    if not _COMPANY_NAME.fullmatch(name) or name.lower() in _NOT_A_NAME:
        return None
    found = re.search(rf"(?<!\w){re.escape(name)}(?!\w)", question, re.IGNORECASE)
    return found.group() if found else None


class ChatState(TypedDict, total=False):
    question: str
    history: list[Turn]
    user_id: UUID
    understood: Question
    stated: list[Preference]  # what this message says about the investor (P13)
    profile: list[StoredPreference]  # the investor's profile after this message
    evidence: list[EvidenceItem]
    series: dict[str, list[SeriesPoint]]  # for the table: metric -> screener.in years, oldest first
    outcome: Outcome
    other_company: str | None  # an out-of-scope question's company, as the model read it
    claims: list[Claim]
    problems: list[Problem]
    attempts: int
    tokens_in: int
    tokens_out: int
    verdict: Verdict
    reply: Reply


class GraphChatEngine:
    """The ChatEngine (app/chat/contract.py) the api calls: one question in, one Reply out."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: Embedder | None,
        llm: StructuredLlm,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._session_factory = session_factory
        self._embedder = embedder
        self._llm = llm
        self._today = today
        self._graph = self._build()

    def _build(self) -> Any:
        graph = StateGraph(ChatState)
        graph.add_node("analyze", self.analyze)
        graph.add_node("update_memory", self.update_memory)
        graph.add_node("retrieve", self.retrieve)
        graph.add_node("generate", self.generate)
        graph.add_node("validate", self.validate)
        graph.add_node("respond", self.respond)
        graph.add_node("abstain", self.abstain)
        graph.add_node("out_of_scope", self.out_of_scope)
        graph.add_node("remembered", self.remembered)
        graph.add_node("forecast", self.forecast)
        graph.add_node("lookup", self.lookup)
        graph.add_edge(START, "analyze")
        graph.add_edge("analyze", "update_memory")
        graph.add_conditional_edges(
            "update_memory",
            self.after_memory,
            {"remembered": "remembered", "forecast": "forecast", "retrieve": "retrieve"},
        )
        graph.add_conditional_edges(
            "retrieve",
            self.grade,
            {"lookup": "lookup", "generate": "generate", "abstain": "abstain"},
        )
        graph.add_edge("lookup", "respond")
        graph.add_edge("generate", "validate")
        graph.add_conditional_edges(
            "validate",
            lambda state: state["verdict"],
            {
                "respond": "respond",
                "retry": "generate",
                "abstain": "abstain",
                "out_of_scope": "out_of_scope",
            },
        )
        for last in ("respond", "abstain", "out_of_scope", "remembered", "forecast"):
            graph.add_edge(last, END)
        return graph.compile()

    async def answer(self, *, question: str, history: list[Turn], user_id: UUID) -> Reply:
        state: ChatState = {
            "question": question,
            "history": history,
            "user_id": user_id,
            "attempts": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "problems": [],
        }
        final = await self._graph.ainvoke(state)
        reply: Reply = final["reply"]
        return reply

    # --- the nodes ---------------------------------------------------------------------------

    async def analyze(self, state: ChatState) -> ChatState:
        return {"understood": understand(state["question"], history=state["history"])}

    async def update_memory(self, state: ChatState) -> ChatState:
        stated = extract_preferences(state["question"])  # the user's own words, nothing else
        async with self._session_factory() as db:
            if stated:
                await remember(db, state["user_id"], stated)
                await db.commit()
            profile = await get_profile(db, state["user_id"])
        return {"stated": stated, "profile": profile}

    def after_memory(self, state: ChatState) -> Literal["remembered", "forecast", "retrieve"]:
        """A message that states preferences and asks nothing (no "?", no stock named, no
        sentence opening with a question or request word) is only confirmed; a future share
        price of a named stock is refused by code; anything else is answered, with the profile
        as context."""
        question = state["question"]
        asks = "?" in question or symbols_in(question) or _ASKS.search(question)
        if state["stated"] and not asks:
            return "remembered"
        understood = state["understood"]
        return "forecast" if understood.wants_forecast and understood.named else "retrieve"

    async def retrieve(self, state: ChatState) -> ChatState:
        question = state["understood"]
        facts: dict[str, list[KeyFact]] = {}
        derived: dict[str, list[DerivedView]] = {}
        events: dict[str, list[StoredEvent]] = {}
        sentiment: dict[str, Sentiment] = {}
        stocks: list[StockRows] = []
        prices: dict[str, PriceSnapshot] = {}
        series: dict[str, list[SeriesPoint]] = {}
        async with self._session_factory() as db:
            for symbol in question.symbols:
                stock = await load_stock(db, symbol)
                if stock is not None:  # pragma: no branch - understand() names only seeded stocks
                    stocks.append(stock)
                    shown, changes = measures_for(
                        question, stock.facts, is_financial=stock.is_financial
                    )
                    facts[symbol] = shown
                    if question.wants_price:  # ADR 025: end-of-day prices, cited to BSE's file
                        prices[symbol] = snapshot(stock.prices, key_facts(stock.facts))
                    others = derived_views(stock.facts, is_financial=stock.is_financial)
                    derived[symbol] = [v for v in others if v.name in OTHER_DERIVED] + changes
                    events[symbol] = stock.events
                    rows = [event.row for event in stock.events]
                    sentiment[symbol] = rolling_sentiment(rows, as_of=self._today())
                    if len(question.symbols) == 1:  # a table is for one stock only
                        for metric in SERIES_METRICS:
                            if metric in question.metrics:
                                series[metric] = await load_series(db, stock.id, metric)
        passages = await self._passages(
            state["question"], question, profile_summary(state["profile"])
        )
        # "Match me" (P14): the verdicts are code's (app/matching/rules.py); the model explains.
        profile = state["profile"]
        wants_match = question.wants_match and bool(profile)
        matches = match_all(profile, stocks, today=self._today()) if wants_match else []
        evidence = build_evidence(
            question=question,
            facts=facts,
            derived=derived,
            passages=passages,
            events=events,
            sentiment=sentiment,
            matches=matches,
            prices=prices,
        )
        return {"evidence": evidence, "series": series}

    async def _passages(self, text: str, question: Question, profile: str) -> list[Result]:
        """The closest passages of the question's stocks, or none if search is switched off or
        unavailable (the facts can still answer; a failed search must not fail the question)."""
        if self._embedder is None:
            return []
        only = question.symbols[0] if len(question.symbols) == 1 else None
        try:
            results = await search(
                self._session_factory,
                self._embedder,
                text,
                symbol=only,
                today=self._today(),
                k=PASSAGES,
                profile=profile,
            )
        except Exception as error:
            logger.warning("chat_search_unavailable", extra={"error": type(error).__name__})
            return []
        return [
            r
            for r in results
            if r.passage.symbol in question.symbols and r.passage.similarity >= MIN_SIMILARITY
        ]

    def grade(self, state: ChatState) -> Literal["lookup", "generate", "abstain"]:
        if lookup_claims(state["understood"], state["evidence"]) is not None:
            return "lookup"
        return "generate" if state["evidence"] else "abstain"

    async def lookup(self, state: ChatState) -> ChatState:
        """The asked figures, stated by code from their evidence items (app/chat/lookup.py)."""
        return {"claims": lookup_claims(state["understood"], state["evidence"]) or []}

    async def generate(self, state: ChatState) -> ChatState:
        attempts = state["attempts"] + 1
        try:
            answer = await self._llm.call(
                system=SYSTEM_PROMPT,
                user=user_message(
                    state["question"],
                    state["history"],
                    evidence_block(state["evidence"]),
                    state["problems"],
                    preferences_text(state["profile"]),
                ),
                tool=answer_tool(),
            )
        except LlmError as error:  # billed, unusable: counted, and treated as a failed answer
            return {
                "attempts": attempts,
                "tokens_in": state["tokens_in"] + error.input_tokens,
                "tokens_out": state["tokens_out"] + error.output_tokens,
                "outcome": "answer",
                "claims": [],
            }
        parsed = parse_answer(answer.input)
        return {
            "attempts": attempts,
            "tokens_in": state["tokens_in"] + answer.input_tokens,
            "tokens_out": state["tokens_out"] + answer.output_tokens,
            "outcome": parsed.outcome,
            "other_company": parsed.other_company,
            "claims": parsed.claims,
        }

    async def validate(self, state: ChatState) -> ChatState:
        if state["outcome"] == "out_of_scope":
            # this question names one of our stocks: it is in scope, the data just lacks it
            ours = bool(symbols_in(state["question"]))
            return {"verdict": "abstain" if ours else "out_of_scope"}
        if state["outcome"] == "not_in_data":
            return {"verdict": "abstain"}
        problems = check_answer(state["claims"], state["evidence"])
        if not problems:
            return {"verdict": "respond", "problems": []}
        logger.info(
            "chat_answer_refused",
            extra={"codes": sorted({p.code for p in problems}), "attempt": state["attempts"]},
        )
        verdict: Verdict = "retry" if state["attempts"] < MAX_ATTEMPTS else "abstain"
        return {"verdict": verdict, "problems": problems}

    async def respond(self, state: ChatState) -> ChatState:
        evidence, question = state["evidence"], state["understood"]
        claims = [*state["claims"], *disclosures(state["claims"], evidence)]
        text, sources = render(claims, evidence)
        by_id = {item.id: item for item in evidence}
        # what the answer itself cites; code's disclosures name other sources on purpose
        own = dict.fromkeys(cid for claim in state["claims"] for cid in claim.citations)
        cited = [by_id[cid] for cid in own]
        documented = ("passage", "event", "match")  # a match verdict carries code's reasons
        if question.wants_reason and not any(i.kind in documented for i in cited):
            text = f"{text} {NO_EXPLANATION_TEXT}"
        fits = fits_answer(question, state["series"], cited)
        table = build_table(question, state["series"]) if fits else None
        return {"reply": self._reply(state, text, "answered", sources, table)}

    async def forecast(self, state: ChatState) -> ChatState:
        return {"reply": self._reply(state, FORECAST_TEXT, "abstained", ())}

    async def abstain(self, state: ChatState) -> ChatState:
        return {"reply": self._reply(state, ABSTAIN_TEXT, "abstained", ())}

    async def remembered(self, state: ChatState) -> ChatState:
        return {"reply": self._reply(state, describe(state["stated"]), "remembered", ())}

    async def out_of_scope(self, state: ChatState) -> ChatState:
        company = _named_in(state["question"], state.get("other_company"))
        return {"reply": self._reply(state, out_of_scope_text(company), "out_of_scope", ())}

    def _reply(
        self,
        state: ChatState,
        text: str,
        status: Any,
        sources: tuple[Any, ...],
        table: DataTable | None = None,
    ) -> Reply:
        called = state["attempts"] > 0
        return Reply(
            text=text,
            status=status,
            sources=sources,
            model=self._llm.model if called else None,
            input_tokens=state["tokens_in"],
            output_tokens=state["tokens_out"],
            table=table,
        )
