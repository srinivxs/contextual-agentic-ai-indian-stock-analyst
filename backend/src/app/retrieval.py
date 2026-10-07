"""Search by meaning: find the passages of the filings that answer a question (P10b, ADR 019).

    question ──> its fingerprint (one Bedrock call, no transaction open)
             ──> SQL: completed filings, optionally one stock, this model's fingerprints,
                 the CANDIDATES closest by cosine distance (pgvector's <=>, exact: no index)
             ──> rerank(): deterministic rules, no LLM
             ──> the top k passages, each with its filing, page and the official BSE link

The database does the heavy part (which passages mean something close to the question). The rules
here only reorder the few dozen closest, and they are plain enough to test one by one:

- a small bonus for recent filings (RECENCY_WEIGHT): it decides near-ties, never a clear gap;
- for the chat, a small bonus for passages close in meaning to the investor's remembered profile
  (PROFILE_WEIGHT, P13): the same size of nudge, so it too decides only near-ties, and only among
  passages the question already found;
- at most MAX_PER_DOCUMENT passages from one filing, so one long annual report cannot take
  every slot;
- the same paragraph appearing in two filings (a standard disclaimer, say) is shown once.

P12's chat will use the same search to gather its evidence; this module never calls an LLM.
"""

import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.embeddings import Embedder, vector_literal

CANDIDATES = 50  # how many of the closest passages the rules choose from
MAX_PER_DOCUMENT = 2
RECENCY_WEIGHT = 0.02  # at most this much is added to a similarity between 0 and 1
PROFILE_WEIGHT = 0.02  # at most this much, times the passage's similarity to the profile
EXCERPT_CHARS = 300  # project rule: the UI shows short excerpts and a link, never the whole text

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_MONTH_YEAR = re.compile(r"^([A-Z][a-z]{2}) (\d{4})$")
_YEAR = re.compile(r"\b(\d{4})\b")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class Passage:
    chunk_id: int
    content_hash: str
    symbol: str
    document_id: int
    title: str
    kind: str | None
    period: str | None
    page: int
    text: str
    source_url: str | None
    similarity: float  # 1 - cosine distance: 1 means the same direction
    profile_similarity: float = 0.0  # the same, to the investor's profile; 0 without one


@dataclass(frozen=True)
class Result:
    passage: Passage
    score: float  # similarity plus the recency and profile bonuses


def filing_date(period: str | None) -> date | None:
    """ "Jul 2026" -> 1 Jul 2026; a year alone ("Annual Report 2025") -> the middle of that year;
    an announcement's subject has no date."""
    if period is None:
        return None
    month_year = _MONTH_YEAR.match(period)
    if month_year and month_year.group(1) in _MONTHS:
        return date(int(month_year.group(2)), _MONTHS.index(month_year.group(1)) + 1, 1)
    year = _YEAR.search(period)
    return date(int(year.group(1)), 6, 30) if year else None


def recency(period: str | None, today: date) -> float:
    """1 for a filing from the last year, 0.5 for the year before, 0 for older or unknown."""
    when = filing_date(period)
    if when is None:
        return 0.0
    age = (today - when).days
    if age <= 365:
        return 1.0
    return 0.5 if age <= 730 else 0.0


def _score(p: Passage, today: date) -> float:
    profile = max(p.profile_similarity, 0.0)  # an opposite profile takes nothing away
    return p.similarity + RECENCY_WEIGHT * recency(p.period, today) + PROFILE_WEIGHT * profile


def rerank(passages: list[Passage], *, today: date, k: int) -> list[Result]:
    scored = [Result(p, _score(p, today)) for p in passages]
    scored.sort(key=lambda r: (-r.score, r.passage.chunk_id))  # ties: the same order every time
    chosen: list[Result] = []
    seen_texts: set[str] = set()
    per_document: dict[int, int] = {}
    for result in scored:
        p = result.passage
        if p.content_hash in seen_texts or per_document.get(p.document_id, 0) >= MAX_PER_DOCUMENT:
            continue
        chosen.append(result)
        seen_texts.add(p.content_hash)
        per_document[p.document_id] = per_document.get(p.document_id, 0) + 1
        if len(chosen) == k:
            break
    return chosen


def excerpt(value: str, limit: int = EXCERPT_CHARS) -> str:
    """The passage, tidied, cut at a word boundary near ``limit`` characters."""
    tidy = _SPACES.sub(" ", value).strip()
    if len(tidy) <= limit:
        return tidy
    cut = tidy[:limit]
    if " " in cut:
        cut = cut[: cut.rindex(" ")]
    return cut.rstrip() + "…"


_CLOSEST = text(
    """
    SELECT c.id AS chunk_id, c.content_hash, s.symbol, d.id AS document_id, d.title, d.kind,
           d.period, c.page_number AS page, c.text, d.source_url,
           1 - (e.embedding <=> CAST(:vector AS vector)) AS similarity,
           COALESCE(1 - (e.embedding <=> CAST(:profile AS vector)), 0) AS profile_similarity
    FROM chunks c
    JOIN documents d ON d.id = c.document_id
    JOIN stocks s ON s.id = d.stock_id
    JOIN embeddings e ON e.model = :model AND e.content_hash = c.content_hash
    WHERE d.status = 'completed'
      AND (CAST(:symbol AS text) IS NULL OR s.symbol = :symbol)
    ORDER BY e.embedding <=> CAST(:vector AS vector), c.id
    LIMIT :limit
    """
)


async def search(
    session_factory: async_sessionmaker[AsyncSession],
    embedder: Embedder,
    question: str,
    *,
    symbol: str | None,
    today: date,
    k: int = 5,
    profile: str = "",
) -> list[Result]:
    """``profile``: the investor's profile summary (P13); empty for none, and then no bonus."""
    embedding = await embedder.embed(question)  # before any transaction: no I/O inside one
    profile_vector = (await embedder.embed(profile)).vector if profile else None
    params = {
        "vector": vector_literal(embedding.vector),
        "profile": vector_literal(profile_vector) if profile_vector else None,
        "model": embedder.model,
        "symbol": symbol,
        "limit": CANDIDATES,
    }
    async with session_factory() as db:
        rows = (await db.execute(_CLOSEST, params)).all()
    passages = [
        Passage(
            **{
                **row._mapping,
                "similarity": float(row.similarity),
                "profile_similarity": float(row.profile_similarity),
            }
        )
        for row in rows
    ]
    return rerank(passages, today=today, k=k)
