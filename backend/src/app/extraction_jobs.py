"""Reading facts and events out of a filing: the extract_document job and its timer (P11, ADR 020).

    worker timer ──> extract_document(document), once per extractor version and model
    extract_document ──> the pages worth reading (page_selection), in windows of ~12,000 characters
                     ──> one LLM call per window, a forced tool call filling a fixed form
                     ──> every item through the deterministic validator (fact_validation)
                     ──> accepted facts and events stored with page and quote; the call recorded

WHY THE LLM CANNOT PLANT A NUMBER: it never writes to the database. It proposes items; code keeps
only those whose quote is on the cited page and whose number, currency, unit and period that page
proves. The stock and the document come from the job, never from the model.

WHAT A CALL LEAVES BEHIND: a row in extraction_calls with the tokens AWS billed, how many items were
accepted, and why the others were refused (codes only, never document text). That row is the spend
so far (the spending cap reads it), and it marks the window done: a job stopped by the cap, a
throttled call or an expired pass pays again only for the windows still missing.

A NEW VERSION STARTS OVER: a changed prompt, page selection or vocabulary gets a new
EXTRACTOR_VERSION (and a model change counts too); the document's facts, events and calls from any
other version or model are deleted before the new ones are made, so two generations never mix.
screener.in facts are never touched here.

No transaction is held open across an LLM call: read in one, call with none, write in another.
"""

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, cast

from sqlalchemy import CursorResult, Row, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.extraction_prompts import (
    document_block,
    events_system_prompt,
    events_tool,
    facts_system_prompt,
    facts_tool,
    parse_event_items,
    parse_fact_items,
)
from app.fact_validation import AcceptedFact, Candidate, Rejection, normalise, validate
from app.ingest import JobCannotSucceed, document_id_of
from app.jobs import ClaimedJob, complete, still_mine
from app.llm import LlmError, StructuredLlm, ToolAnswer
from app.page_selection import Window, select_event_pages, select_fact_pages, windows
from app.retrieval import filing_date
from app.vocabulary import EVENT_TYPES, metrics_for

# Bump when the prompts, the page selection or the vocabulary change: every document is read again.
EXTRACTOR_VERSION = "p11-v1"
# After a job for a document is queued, how long before the timer may queue it again (a job that
# failed on an expired pass or the cap is retried after this, not every minute).
RETRY_MINUTES = 10

Pass = Literal["facts", "events"]


@dataclass(frozen=True)
class _Document:
    id: int
    stock_id: int
    symbol: str
    name: str
    is_financial: bool
    title: str
    kind: str | None
    period: str | None
    fetched: date


# --- the timer ------------------------------------------------------------------------------------

_ENQUEUE = text(
    """
    INSERT INTO jobs (kind, payload, dedupe_key)
    SELECT 'extract_document',
           jsonb_build_object('document_id', d.id),
           'extract_document:' || d.id || ':' || :version || ':' || :model
    FROM documents d
    WHERE d.status = 'completed'
      AND NOT EXISTS (
        SELECT 1 FROM jobs j
        WHERE j.dedupe_key = 'extract_document:' || d.id || ':' || :version || ':' || :model
          AND (j.status = 'completed' OR j.created_at > now() - make_interval(mins => :minutes))
      )
    ORDER BY d.id
    ON CONFLICT (dedupe_key) WHERE status IN ('pending', 'processing') DO NOTHING
    """
)


async def enqueue_extractions(
    db: AsyncSession, *, version: str, model: str, retry_minutes: int = RETRY_MINUTES
) -> int:
    """Queue extract_document for each ingested document not yet read by this version and model."""
    params = {"version": version, "model": model, "minutes": retry_minutes}
    result = cast("CursorResult[Any]", await db.execute(_ENQUEUE, params))
    return result.rowcount


# --- event dates (by code, never by the model) ----------------------------------------------

_MONTH_NAMES = {
    name: number
    for number, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}
_MONTH = "(" + "|".join(sorted(_MONTH_NAMES, key=len, reverse=True)) + ")"
_ORDINAL = r"(?:st|nd|rd|th)?"
_DATE_PATTERNS = [  # (pattern, which groups are day, month, year)
    (re.compile(rf"\b{_MONTH}\.?\s+(\d{{1,2}}){_ORDINAL},?\s+(\d{{4}})\b", re.I), (2, 1, 3)),
    (re.compile(rf"\b(\d{{1,2}}){_ORDINAL}\s+{_MONTH}\.?,?\s+(\d{{4}})\b", re.I), (1, 2, 3)),
    (re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b"), (1, 2, 3)),  # Indian: day first
]


def _date_in(value: str) -> date | None:
    """The first real date written in ``value``, in any of the usual forms."""
    found: list[tuple[int, date]] = []
    for pattern, (day, month, year) in _DATE_PATTERNS:
        for match in pattern.finditer(value):
            month_text = match.group(month)
            number = _MONTH_NAMES.get(month_text.lower()) or int(month_text)
            try:
                found.append(
                    (match.start(), date(int(match.group(year)), number, int(match.group(day))))
                )
            except ValueError:  # 31/02/2026: not a date
                continue
    return min(found)[1] if found else None


def event_date(
    kind: str | None, period: str | None, first_page: str, *, fetched: date
) -> tuple[date, str]:
    """When an event happened, and how we know: an earnings call's or presentation's month
    ('document'), else the first date written on the first page ('text'), else the day the filing
    was fetched ('fetched')."""
    if kind in ("transcript", "presentation"):
        month = filing_date(period)
        if month is not None:
            return month, "document"
    written = _date_in(first_page)
    if written is not None:
        return written, "text"
    return fetched, "fetched"


# --- the job ------------------------------------------------------------------------------------

_DOCUMENT = text(
    """
    SELECT d.id, d.stock_id, s.symbol, s.name, s.is_financial, d.title, d.kind, d.period,
           d.status, d.created_at
    FROM documents d JOIN stocks s ON s.id = d.stock_id
    WHERE d.id = :id
    """
)
_PAGES = text(
    "SELECT page_number, text FROM document_pages WHERE document_id = :id ORDER BY page_number"
)
_OLD_CALLS = text(
    "SELECT 1 FROM extraction_calls WHERE document_id = :id "
    "AND (version <> :version OR model <> :model) LIMIT 1"
)
_FORGET = [
    text("DELETE FROM facts WHERE document_id = :id AND source = 'filing'"),
    text("DELETE FROM events WHERE document_id = :id"),
    text("DELETE FROM extraction_calls WHERE document_id = :id"),
]
_DONE = text(
    "SELECT pass AS pass_name, first_page FROM extraction_calls "
    "WHERE document_id = :id AND version = :version AND model = :model"
)
_SPENT = text(
    "SELECT coalesce(sum(input_tokens), 0) AS tokens_in, "
    "coalesce(sum(output_tokens), 0) AS tokens_out FROM extraction_calls WHERE model = :model"
)
# A figure found on two pages of one window pair keeps the lower page, whatever order they finish.
_INSERT_FACT = text(
    """
    INSERT INTO facts (stock_id, source, document_id, page_number, quote, metric, period,
                       period_end, basis, currency, unit, value, reported_text, model, version)
    VALUES (:stock_id, 'filing', :document_id, :page, :quote, :metric, :period, :period_end,
            :basis, :currency, :unit, :value, :reported_text, :model, :version)
    ON CONFLICT (document_id, metric, period, basis, currency) WHERE source = 'filing'
    DO UPDATE SET page_number = EXCLUDED.page_number, quote = EXCLUDED.quote,
                  value = EXCLUDED.value, unit = EXCLUDED.unit,
                  reported_text = EXCLUDED.reported_text, updated_at = now()
    WHERE EXCLUDED.page_number < facts.page_number
    """
)
_INSERT_EVENT = text(
    """
    INSERT INTO events (stock_id, document_id, page_number, event_type, sentiment, impact,
                        event_date, date_source, summary, quote, model, version)
    VALUES (:stock_id, :document_id, :page, :event_type, :sentiment, :impact, :event_date,
            :date_source, :summary, :quote, :model, :version)
    ON CONFLICT (document_id, event_type) DO NOTHING
    """
)
_INSERT_CALL = text(
    """
    INSERT INTO extraction_calls (document_id, pass, first_page, last_page, model, version,
                                  input_tokens, output_tokens, accepted, rejections)
    VALUES (:document_id, :pass, :first_page, :last_page, :model, :version, :tokens_in,
            :tokens_out, :accepted, CAST(:rejections AS jsonb))
    ON CONFLICT DO NOTHING
    """
)


def _budget_error(budget: Decimal) -> JobCannotSucceed:
    return JobCannotSucceed(
        f"the spending cap is used up: ${budget} (EXTRACTION_BUDGET_USD); raise it to go on"
    )


async def _spent_usd(
    session_factory: async_sessionmaker[AsyncSession], model: str, prices: tuple[Decimal, Decimal]
) -> Decimal:
    """What this model's calls have cost so far, from the tokens AWS billed."""
    async with session_factory() as db:
        row = (await db.execute(_SPENT, {"model": model})).one()
    spent: Decimal = (row.tokens_in * prices[0] + row.tokens_out * prices[1]) / Decimal(1_000_000)
    return spent


async def _read(llm: StructuredLlm, document: _Document, kind: Pass, window: Window) -> ToolAnswer:
    """One LLM call for one window. The document text travels only inside <document> tags."""
    intro = f"Company: {document.name} ({document.symbol}). Filing: {document.title}.\n"
    if kind == "facts":
        metrics = metrics_for(document.is_financial)
        return await llm.call(
            system=facts_system_prompt([(m.name, m.description) for m in metrics]),
            user=intro + document_block(window.text),
            tool=facts_tool([m.name for m in metrics]),
        )
    return await llm.call(
        system=events_system_prompt(list(EVENT_TYPES)),
        user=intro + document_block(window.text),
        tool=events_tool(list(EVENT_TYPES)),
    )


def _checked_facts(
    answer: ToolAnswer, document: _Document, window_pages: dict[int, str]
) -> tuple[list[AcceptedFact], list[dict[str, Any]]]:
    names = [metric.name for metric in metrics_for(document.is_financial)]
    accepted: list[AcceptedFact] = []
    refused: list[dict[str, Any]] = []
    for item in parse_fact_items(answer.input, metrics=names):
        result = validate(
            Candidate(
                metric=item.metric,
                value_text=item.value_text,
                unit_word=item.unit_word,
                currency=item.currency,
                period_text=item.period_text,
                basis=item.basis,
                page=item.page,
                quote=item.quote,
            ),
            pages=window_pages,
            is_financial=document.is_financial,
            document_month=filing_date(document.period),
        )
        if isinstance(result, Rejection):
            refused.append({"code": result.code, "item": result.metric, "page": result.page})
        else:
            accepted.append(result)
    return accepted, refused


def _checked_events(
    answer: ToolAnswer, window_pages: dict[int, str]
) -> tuple[list[Any], list[dict[str, Any]]]:
    accepted: list[Any] = []
    refused: list[dict[str, Any]] = []
    for item in parse_event_items(answer.input, event_types=list(EVENT_TYPES)):
        page = window_pages.get(item.page)
        if page is None:
            code = "page_outside_window"
        elif normalise(item.quote) not in normalise(page):
            code = "quote_not_on_page"
        else:
            accepted.append(item)
            continue
        refused.append({"code": code, "item": item.event_type, "page": item.page})
    return accepted, refused


async def _store(
    db: AsyncSession,
    document: _Document,
    kind: Pass,
    window: Window,
    outcome: ToolAnswer | LlmError,
    *,
    pages: dict[int, str],
    model: str,
    version: str,
) -> None:
    """Write one window's accepted items and its call row, in the caller's transaction."""
    window_pages = {number: pages[number] for number in window.pages}
    if isinstance(outcome, LlmError):  # billed, unusable: recorded so the cap counts it
        tokens = (outcome.input_tokens, outcome.output_tokens)
        accepted_count = 0
        refused: list[dict[str, Any]] = [
            {"code": "unusable_answer", "item": "", "page": window.pages[0]}
        ]
    elif kind == "facts":
        tokens = (outcome.input_tokens, outcome.output_tokens)
        facts, refused = _checked_facts(outcome, document, window_pages)
        for fact in facts:
            await db.execute(
                _INSERT_FACT,
                {
                    "stock_id": document.stock_id,
                    "document_id": document.id,
                    "page": fact.page,
                    "quote": fact.quote,
                    "metric": fact.metric,
                    "period": fact.period.code,
                    "period_end": fact.period.end,
                    "basis": fact.basis,
                    "currency": fact.currency,
                    "unit": fact.unit,
                    "value": fact.value.quantize(Decimal("0.0001")),
                    "reported_text": fact.reported_text,
                    "model": model,
                    "version": version,
                },
            )
        accepted_count = len(facts)
    else:
        tokens = (outcome.input_tokens, outcome.output_tokens)
        events, refused = _checked_events(outcome, window_pages)
        when, source = event_date(
            document.kind, document.period, pages.get(1, ""), fetched=document.fetched
        )
        for event in events:
            await db.execute(
                _INSERT_EVENT,
                {
                    "stock_id": document.stock_id,
                    "document_id": document.id,
                    "page": event.page,
                    "event_type": event.event_type,
                    "sentiment": event.sentiment,
                    "impact": event.impact,
                    "event_date": when,
                    "date_source": source,
                    "summary": event.summary,
                    "quote": event.quote,
                    "model": model,
                    "version": version,
                },
            )
        accepted_count = len(events)
    await db.execute(
        _INSERT_CALL,
        {
            "document_id": document.id,
            "pass": kind,
            "first_page": window.pages[0],
            "last_page": window.pages[-1],
            "model": model,
            "version": version,
            "tokens_in": tokens[0],
            "tokens_out": tokens[1],
            "accepted": accepted_count,
            "rejections": json.dumps(refused),
        },
    )


async def _load(
    session_factory: async_sessionmaker[AsyncSession],
    job: ClaimedJob,
    *,
    model: str,
    version: str,
) -> tuple[_Document, dict[int, str], set[tuple[str, int]]] | None:
    """The document, its pages and the windows already done; None if the job is not ours now."""
    document_id = document_id_of(job)
    async with session_factory() as db:
        row: Row[Any] | None = (await db.execute(_DOCUMENT, {"id": document_id})).one_or_none()
        if row is None or row.status != "completed":
            raise JobCannotSucceed("the document is not ingested (yet)")
        if not await still_mine(db, job):
            return None
        keys = {"id": document_id, "version": version, "model": model}
        if (await db.execute(_OLD_CALLS, keys)).first() is not None:
            for statement in _FORGET:  # another version or model read it: start over
                await db.execute(statement, keys)
        await db.commit()
        pages = {r.page_number: r.text for r in await db.execute(_PAGES, keys)}
        done = {(r.pass_name, r.first_page) for r in await db.execute(_DONE, keys)}
    created_at: datetime = row.created_at
    document = _Document(
        id=row.id,
        stock_id=row.stock_id,
        symbol=row.symbol,
        name=row.name,
        is_financial=row.is_financial,
        title=row.title,
        kind=row.kind,
        period=row.period,
        fetched=created_at.date(),
    )
    return document, pages, done


async def extract_document(
    session_factory: async_sessionmaker[AsyncSession],
    llm: StructuredLlm,
    job: ClaimedJob,
    *,
    version: str,
    budget_usd: Decimal,
    prices: tuple[Decimal, Decimal],
    concurrency: int,
) -> None:
    loaded = await _load(session_factory, job, model=llm.model, version=version)
    if loaded is None:
        return
    document, pages, done = loaded
    plan: list[tuple[Pass, Window]] = [
        ("facts", window) for window in windows(pages, select_fact_pages(document.kind, pages))
    ] + [("events", window) for window in windows(pages, select_event_pages(document.kind, pages))]
    todo = [(kind, window) for kind, window in plan if (kind, window.pages[0]) not in done]

    errors: list[BaseException] = []
    for start in range(0, len(todo), concurrency):
        if await _spent_usd(session_factory, llm.model, prices) >= budget_usd:
            raise _budget_error(budget_usd)
        batch = todo[start : start + concurrency]
        outcomes = await asyncio.gather(
            *(_read(llm, document, kind, window) for kind, window in batch), return_exceptions=True
        )
        for (kind, window), outcome in zip(batch, outcomes, strict=True):
            if isinstance(outcome, BaseException) and not isinstance(outcome, LlmError):
                errors.append(outcome)  # throttled, expired pass: retried later, unpaid for
                continue
            async with session_factory() as db:
                if not await still_mine(db, job):
                    return  # another worker has this job now
                await _store(
                    db, document, kind, window, outcome, pages=pages, model=llm.model,
                    version=version,
                )  # fmt: skip
                await db.commit()
    if errors:
        raise errors[0]
    async with session_factory() as db:
        await complete(db, job)  # fenced: does nothing if another worker holds the job now
        await db.commit()
