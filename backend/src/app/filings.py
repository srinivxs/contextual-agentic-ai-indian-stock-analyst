"""Official filings, found and fetched automatically (ADR 018).

    timer ──> discover_filings(symbol) ──> screener.in company page ──> official BSE links
                                          ──> one fetch_filing job per link not yet stored
          ──> fetch_filing(url) ──> the PDF from www.bseindia.com ──> document + ingest job

screener.in is only a signpost. From its page we read the links in the documents section and keep
those that point at a PDF on www.bseindia.com, where companies file. We never store the page, and
never keep or show screener's own numbers or opinions: every fact is later cited to the official
filing and its page.

What is chosen per stock: the 4 newest earnings-call transcripts and the newest annual report,
BSE-hosted only (company websites refused automated downloads in P0): five per stock, fifteen
in all.

Politeness: one page per stock per day (the timer), an honest User-Agent (app/polite_fetch.py), a
path screener's robots.txt allows (/company/<symbol>/), and a switch that is off by default.
"""

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, cast

import httpx
from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.blobs import BlobStore, blob_key_for
from app.documents import PDF_SIGNATURE, Upload, record_upload, stock_id
from app.ingest import JobCannotSucceed
from app.jobs import ClaimedJob, complete, still_mine
from app.polite_fetch import FetchRefused, FetchTooLarge, polite_get

SCREENER_URL = "https://www.screener.in/company/{symbol}/consolidated/"
PAGE_LIMIT = 3 * 1024 * 1024  # a company page is a few hundred kilobytes
TRANSCRIPTS = 4
ANNUAL_REPORTS = 1

_FILE = r"[0-9A-Fa-f-]{32,40}\.pdf"
_OFFICIAL_PDF = re.compile(
    r"^https://www\.bseindia\.com/(?:"
    rf"stockinfo/AnnPdfOpen\.aspx\?Pname={_FILE}"
    rf"|xml-data/corpfiling/(?:AttachHis|AttachLive)/{_FILE}"
    r")$"
)

KIND_WORDS = {"transcript": "earnings call transcript", "annual_report": "annual report"}


@dataclass(frozen=True)
class FilingLink:
    kind: str  # "transcript" or "annual_report"
    label: str  # as the page shows it: "Jul 2026", "Financial Year 2026"
    url: str


def is_official_pdf_url(url: str) -> bool:
    """A PDF on www.bseindia.com at one of the two addresses BSE serves filings from. Nothing else:
    no other host, no port, no userinfo, no extra query, no path tricks."""
    return bool(_OFFICIAL_PDF.match(url))


def is_screener_page(symbol: str) -> Callable[[str], bool]:
    """The company page for this one symbol, with or without the consolidated view."""
    base = f"https://www.screener.in/company/{symbol}/"
    return lambda url: url in (base, base + "consolidated/")


class _DocumentsParser(HTMLParser):
    """Reads only the links of the #documents section's annual-reports and concalls lists."""

    _VOID = frozenset({"br", "img", "hr", "input", "meta", "link", "wbr", "source"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[FilingLink] = []
        self._depth = 0
        self._documents_at: int | None = None  # depth at which #documents opened
        self._block: str | None = None  # "annual" or "concalls"
        self._block_at: int | None = None
        self._href: str | None = None  # the <a> being read
        self._text: list[str] = []
        self._in_nested_div = False
        self._month: list[str] | None = None  # the concall entry's first <div>
        self._reading_month = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag not in self._VOID:
            self._depth += 1
        if attributes.get("id") == "documents" and self._documents_at is None:
            self._documents_at = self._depth
            return
        if self._documents_at is None:
            return
        classes = (attributes.get("class") or "").split()
        if tag == "div" and self._block is None:
            if "annual-reports" in classes:
                self._block, self._block_at = "annual", self._depth
            elif "concalls" in classes:
                self._block, self._block_at = "concalls", self._depth
            return
        if self._block == "concalls" and tag == "li":
            self._month = []
        elif tag == "a" and self._block:
            self._href, self._text, self._in_nested_div = attributes.get("href") or "", [], False
        elif tag == "div" and self._href is not None:
            self._in_nested_div = True  # "from bse" inside an annual-report link
        elif tag == "div" and self._month == [] and not self._reading_month:
            self._reading_month = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            label = " ".join("".join(self._text).split())
            if self._block == "annual":
                self.links.append(FilingLink("annual_report", label, self._href))
            elif self._block == "concalls" and "transcript" in label.lower():
                month = " ".join("".join(self._month or []).split())
                self.links.append(FilingLink("transcript", month, self._href))
            self._href = None
        elif tag == "div" and self._in_nested_div:
            self._in_nested_div = False
        elif tag == "div" and self._reading_month:
            self._reading_month = False
        if tag in self._VOID:
            return
        if self._block_at is not None and self._depth == self._block_at:
            self._block = self._block_at = None
        if self._documents_at is not None and self._depth == self._documents_at:
            self._documents_at = None
        self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._href is not None and not self._in_nested_div:
            self._text.append(data)
        elif self._reading_month and self._month is not None:
            self._month.append(data)


def parse_documents(html: str) -> list[FilingLink]:
    parser = _DocumentsParser()
    parser.feed(html)
    parser.close()
    return parser.links


def select_filings(links: list[FilingLink]) -> list[FilingLink]:
    """The newest official transcripts and annual reports (the page lists newest first)."""
    seen: set[str] = set()
    official = []
    for link in links:
        if is_official_pdf_url(link.url) and link.url not in seen:
            seen.add(link.url)
            official.append(link)
    transcripts = [link for link in official if link.kind == "transcript"][:TRANSCRIPTS]
    reports = [link for link in official if link.kind == "annual_report"][:ANNUAL_REPORTS]
    return transcripts + reports


def title_for(symbol: str, link: FilingLink) -> str:
    label = " ".join(link.label.split())
    return f"{symbol} {KIND_WORDS[link.kind]}, {label}"[:200]


def fetch_key(url: str) -> str:
    return "fetch_filing:" + hashlib.sha256(url.encode("utf-8")).hexdigest()


# --- the timer -----------------------------------------------------------------------------------

_ENQUEUE_DISCOVERY = text(
    """
    INSERT INTO jobs (kind, payload, dedupe_key)
    SELECT 'discover_filings',
           jsonb_build_object('symbol', s.symbol),
           'discover_filings:' || s.symbol
    FROM stocks s
    WHERE NOT EXISTS (
        SELECT 1 FROM jobs j
        WHERE j.dedupe_key = 'discover_filings:' || s.symbol
          AND j.created_at > now() - make_interval(hours => :hours)
    )
    ON CONFLICT (dedupe_key) WHERE status IN ('pending', 'processing') DO NOTHING
    """
)


async def enqueue_discovery(db: AsyncSession, *, every_hours: int) -> int:
    """Queue one discovery per stock unless one was queued within ``every_hours``."""
    params = {"hours": every_hours}
    result = cast("CursorResult[Any]", await db.execute(_ENQUEUE_DISCOVERY, params))
    return result.rowcount


# --- the two jobs -------------------------------------------------------------------------------

_KNOWN = text("SELECT 1 FROM documents WHERE source_url = :url")

_ENQUEUE_FETCH = text(
    "INSERT INTO jobs (kind, payload, dedupe_key) "
    "SELECT 'fetch_filing', jsonb_build_object('symbol', CAST(:symbol AS text), "
    "'url', CAST(:url AS text), 'kind', CAST(:kind AS text), 'label', CAST(:label AS text)), :key "
    "WHERE NOT EXISTS (SELECT 1 FROM documents WHERE source_url = :url) "
    "ON CONFLICT (dedupe_key) WHERE status IN ('pending', 'processing') DO NOTHING"
)


def _payload(job: ClaimedJob, *keys: str) -> list[str]:
    try:
        values = [job.payload[key] for key in keys]
    except (KeyError, TypeError) as error:
        raise JobCannotSucceed(f"the job needs {', '.join(keys)}") from error
    if not all(isinstance(value, str) for value in values):
        raise JobCannotSucceed(f"the job needs {', '.join(keys)} as text")
    return values


async def discover(
    session_factory: async_sessionmaker[AsyncSession], http: httpx.AsyncClient, job: ClaimedJob
) -> None:
    [symbol] = _payload(job, "symbol")
    async with session_factory() as db:
        stock = await stock_id(db, symbol)
    if stock is None:
        raise JobCannotSucceed(f"no such stock: {symbol[:20]}")

    page = await polite_get(
        http, SCREENER_URL.format(symbol=symbol), allowed=is_screener_page(symbol), limit=PAGE_LIMIT
    )
    links = select_filings(parse_documents(page.decode("utf-8", errors="replace")))

    async with session_factory() as db:
        if not await still_mine(db, job):
            return
        for link in links:
            await db.execute(
                _ENQUEUE_FETCH,
                {
                    "symbol": symbol,
                    "url": link.url,
                    "kind": link.kind,
                    "label": link.label,
                    "key": fetch_key(link.url),
                },
            )
        await complete(db, job)
        await db.commit()


async def fetch(
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    store: BlobStore,
    job: ClaimedJob,
    *,
    limit: int,
) -> None:
    symbol, url, kind, label = _payload(job, "symbol", "url", "kind", "label")
    if not is_official_pdf_url(url) or kind not in KIND_WORDS:
        raise JobCannotSucceed("not an official filing address")

    async with session_factory() as db:
        stock = await stock_id(db, symbol)
        known = (await db.execute(_KNOWN, {"url": url})).first() is not None
    if stock is None:
        raise JobCannotSucceed(f"no such stock: {symbol[:20]}")

    if not known:
        try:
            data = await polite_get(http, url, allowed=is_official_pdf_url, limit=limit)
        except FetchRefused as error:
            raise JobCannotSucceed(str(error)) from error
        except FetchTooLarge as error:
            raise JobCannotSucceed(f"the filing is larger than {limit} bytes") from error
        if not data.startswith(PDF_SIGNATURE):
            raise JobCannotSucceed("the filing address did not return a PDF (not a PDF)")
        upload = Upload(data=data, sha256=hashlib.sha256(data).hexdigest())
        key = blob_key_for(upload.sha256)
        await store.put(key, upload.data)  # before the transaction: no transaction spans I/O

    async with session_factory() as db:
        if not await still_mine(db, job):
            return
        if not known:
            await record_upload(
                db,
                stock=stock,
                user_id=None,
                title=title_for(symbol, FilingLink(kind, label, url)),
                upload=upload,
                blob_key=key,
                source="bse",
                source_url=url,
            )
        await complete(db, job)
        await db.commit()
