"""The grounded chat as a small, explicit LangGraph workflow (P12, ADR 003).

    analyze ─> update_memory ─┬─> retrieve ─> grade ─┬─> generate ─> validate ─┬─> respond
                              │                      │                         ├─> generate (retry)
                              │                      └─> abstain <─────────────┼─> abstain
                              └─> remembered                                   └─> out_of_scope

- analyze (code): which stocks, metrics, periods (app/chat/understand.py). No LLM: rule 8.
- update_memory (code, P13): the preferences the user's own message states (app/memory/extract.py)
  are saved, and the profile is loaded. It reads state["question"] and nothing else: never the
  history, the evidence or a reply, so no document can write memory (ADR 022). A message that
  only states preferences ends at remembered: "Noted. I'll remember: ...", with no LLM call.
- retrieve (code): the stocks' chosen facts and derived values (app/insights.py), the passages
  closest in meaning (app/retrieval.py) and, for news questions, the events and the rolling news
  sentiment, and for "Match me" questions the verdicts of app/matching/rules.py, numbered F#, D#,
  M#, N#, E# (app/chat/evidence.py).
- grade (code): no evidence at all means "I don't have that in the data", without an LLM call.
- generate (LLM): one call, a forced tool filling a fixed form (app/chat/prompts.py).
- validate (code): every claim cites real evidence and every number is in what it cites
  (app/chat/answer_check.py). A failure gets one retry, told what failed; a second failure abstains.
- respond (code): the [n] markers and the numbered sources (app/chat/render.py).

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
from app.chat.contract import ABSTAIN_TEXT, OUT_OF_SCOPE_TEXT, Reply, Turn
from app.chat.evidence import EvidenceItem, build_evidence, evidence_block
from app.chat.prompts import SYSTEM_PROMPT, Outcome, answer_tool, parse_answer, user_message
from app.chat.render import render
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

Verdict = Literal["respond", "retry", "abstain", "out_of_scope"]


class ChatState(TypedDict, total=False):
    question: str
    history: list[Turn]
    user_id: UUID
    understood: Question
    stated: list[Preference]  # what this message says about the investor (P13)
    profile: list[StoredPreference]  # the investor's profile after this message
    evidence: list[EvidenceItem]
    outcome: Outcome
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
        graph.add_edge(START, "analyze")
        graph.add_edge("analyze", "update_memory")
        graph.add_conditional_edges(
            "update_memory",
            self.only_preferences,
            {"remembered": "remembered", "retrieve": "retrieve"},
        )
        graph.add_conditional_edges(
            "retrieve", self.grade, {"generate": "generate", "abstain": "abstain"}
        )
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
        for last in ("respond", "abstain", "out_of_scope", "remembered"):
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

    def only_preferences(self, state: ChatState) -> Literal["remembered", "retrieve"]:
        """A message that states preferences and asks nothing (no "?", no stock named, no
        sentence opening with a question or request word) is only confirmed; anything else is
        answered, with the profile as context."""
        question = state["question"]
        asks = "?" in question or symbols_in(question) or _ASKS.search(question)
        return "remembered" if state["stated"] and not asks else "retrieve"

    async def retrieve(self, state: ChatState) -> ChatState:
        question = state["understood"]
        facts: dict[str, list[KeyFact]] = {}
        derived: dict[str, list[DerivedView]] = {}
        events: dict[str, list[StoredEvent]] = {}
        sentiment: dict[str, Sentiment] = {}
        stocks: list[StockRows] = []
        prices: dict[str, PriceSnapshot] = {}
        async with self._session_factory() as db:
            for symbol in question.symbols:
                stock = await load_stock(db, symbol)
                if stock is not None:  # pragma: no branch - understand() names only seeded stocks
                    stocks.append(stock)
                    facts[symbol] = key_facts(stock.facts)
                    if question.wants_price:  # ADR 025: end-of-day prices, cited to BSE's file
                        prices[symbol] = snapshot(stock.prices, facts[symbol])
                    derived[symbol] = derived_views(stock.facts, is_financial=stock.is_financial)
                    events[symbol] = stock.events
                    rows = [event.row for event in stock.events]
                    sentiment[symbol] = rolling_sentiment(rows, as_of=self._today())
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
        return {"evidence": evidence}

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

    def grade(self, state: ChatState) -> Literal["generate", "abstain"]:
        return "generate" if state["evidence"] else "abstain"

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
            "claims": parsed.claims,
        }

    async def validate(self, state: ChatState) -> ChatState:
        if state["outcome"] == "out_of_scope":
            return {"verdict": "out_of_scope"}
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
        text, sources = render(state["claims"], state["evidence"])
        return {"reply": self._reply(state, text, "answered", sources)}

    async def abstain(self, state: ChatState) -> ChatState:
        return {"reply": self._reply(state, ABSTAIN_TEXT, "abstained", ())}

    async def remembered(self, state: ChatState) -> ChatState:
        return {"reply": self._reply(state, describe(state["stated"]), "remembered", ())}

    async def out_of_scope(self, state: ChatState) -> ChatState:
        return {"reply": self._reply(state, OUT_OF_SCOPE_TEXT, "out_of_scope", ())}

    def _reply(self, state: ChatState, text: str, status: Any, sources: tuple[Any, ...]) -> Reply:
        called = state["attempts"] > 0
        return Reply(
            text=text,
            status=status,
            sources=sources,
            model=self._llm.model if called else None,
            input_tokens=state["tokens_in"],
            output_tokens=state["tokens_out"],
        )
