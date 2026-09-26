"""Official filings, found and fetched automatically (ADR 018).

    timer ──> discover_filings(symbol) ──> screener.in company page ──> official BSE links
                                          ──> one fetch_filing job per link not yet stored
          ──> fetch_filing(url) ──> the PDF from www.bseindia.com ──> document + ingest job

screener.in is only a signpost. From its page we read the links in the documents section and keep
those that point at a PDF on www.bseindia.com, where companies file. We never store the page, and
never keep or show screener's own numbers or opinions: every fact is later cited to the official
filing and its page.

What is chosen per stock (FILINGS_YEARS, default 3; the owner asked for three years of every
PDF): every earnings-call transcript and investor presentation dated within the window, the
annual reports among the newest FILINGS_YEARS listed, and the announcements the page shows.
BSE-hosted only: company websites refused automated downloads in P0, and credit-rating agencies'
sites are not on the allow-list. About 85 PDFs for the three stocks.

Politeness: one page per stock per day (the timer), an honest User-Agent (app/polite_fetch.py), a
path screener's robots.txt allows (/company/<symbol>/), a pause before every download, and a
switch that is off by default.

FOUND IN THE FIRST REAL RUN: transcripts are linked through BSE's AnnPdfOpen.aspx script page,
which redirects to the file at xml-data/corpfiling/AttachHis/<same id>.pdf, but often answered
406 Not Acceptable instead. The file addresses answered every time. So each link is turned into
its direct file address (``file_candidates``); a filing made today may still be under AttachLive/.
"""

import asyncio
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Any, cast

import httpx
from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.blobs import BlobStore, blob_key_for
from app.documents import PDF_SIGNATURE, PdfFile, record_document, stock_id
from app.ingest import JobCannotSucceed
from app.jobs import ClaimedJob, complete, still_mine
from app.polite_fetch import FetchRefused, FetchTooLarge, polite_get
from app.screener_facts import store_screener_facts

SCREENER_URL = "https://www.screener.in/company/{symbol}/consolidated/"
PAGE_LIMIT = 3 * 1024 * 1024  # a company page is a few hundred kilobytes

_MONTHS = {
    name: number
    for number, name in enumerate(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        start=1,
    )
}
_MONTH_YEAR = re.compile(r"^([A-Z][a-z]{2}) (\d{4})$")

_FILE = r"[0-9A-Fa-f-]{32,40}\.pdf"
_FILE_ID = re.compile(r"(?:Pname=|/AttachHis/|/AttachLive/)([0-9A-Fa-f-]{32,40})\.pdf$")
_FILES = "https://www.bseindia.com/xml-data/corpfiling/"

_OFFICIAL_PDF = re.compile(
    r"^https://www\.bseindia\.com/(?:"
    rf"stockinfo/AnnPdfOpen\.aspx\?Pname={_FILE}"
    rf"|xml-data/corpfiling/(?:AttachHis|AttachLive)/{_FILE}"
    r")$"
)

KIND_WORDS = {
    "transcript": "earnings call transcript",
    "presentation": "investor presentation",
    "annual_report": "annual report",
    "announcement": "announcement",
}


@dataclass(frozen=True)
class FilingLink:
    kind: str  # "transcript", "presentation", "annual_report" or "announcement"
    label: str  # as the page shows it: "Jul 2026", "Financial Year 2026", an announcement's subject
    url: str


def is_official_pdf_url(url: str) -> bool:
    """A PDF on www.bseindia.com at one of the two addresses BSE serves filings from. Nothing else:
    no other host, no port, no userinfo, no extra query, no path tricks."""
    return bool(_OFFICIAL_PDF.match(url))


def file_candidates(url: str) -> list[str]:
    """The direct file addresses to try for an official filing link, best first.

    A script-page link or a historical file: AttachHis/ first, then AttachLive/. A live file: the
    other way round. Anything that is not an official address has none.
    """
    if not is_official_pdf_url(url):
        return []
    match = _FILE_ID.search(url)
    if match is None:  # pragma: no cover - every official address carries an id
        return []
    historical = f"{_FILES}AttachHis/{match.group(1)}.pdf"
    live = f"{_FILES}AttachLive/{match.group(1)}.pdf"
    return [live, historical] if "/AttachLive/" in url else [historical, live]


def is_screener_page(symbol: str) -> Callable[[str], bool]:
    """The company page for this one symbol, with or without the consolidated view."""
    base = f"https://www.screener.in/company/{symbol}/"
    return lambda url: url in (base, base + "consolidated/")


class _DocumentsParser(HTMLParser):
    """Reads only the links of the #documents section: announcements, annual reports, concalls.

    Credit ratings are skipped: they live on the rating agencies' sites, which are not allowed.
    """

    _VOID = frozenset({"br", "img", "hr", "input", "meta", "link", "wbr", "source"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[FilingLink] = []
        self._depth = 0
        self._documents_at: int | None = None  # depth at which #documents opened
        self._block: str | None = None  # "announcements", "annual", "concalls" or "skip"
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
        if tag == "div" and self._block is None and "documents" in classes:
            if "annual-reports" in classes:
                self._block = "annual"
            elif "concalls" in classes:
                self._block = "concalls"
            elif "credit-ratings" in classes:
                self._block = "skip"
            else:
                self._block = "announcements"
            self._block_at = self._depth
            return
        if self._block == "concalls" and tag == "li":
            self._month = []
        elif tag == "a" and self._block in ("announcements", "annual", "concalls"):
            self._href, self._text, self._in_nested_div = attributes.get("href") or "", [], False
        elif tag == "div" and self._href is not None:
            self._in_nested_div = True  # "from bse" inside an annual-report link
        elif tag == "div" and self._month == [] and not self._reading_month:
            self._reading_month = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            label = " ".join("".join(self._text).split())
            month = " ".join("".join(self._month or []).split())
            if self._block == "annual":
                self.links.append(FilingLink("annual_report", label, self._href))
            elif self._block == "announcements" and label:
                self.links.append(FilingLink("announcement", label, self._href))
            elif self._block == "concalls" and "transcript" in label.lower():
                self.links.append(FilingLink("transcript", month, self._href))
            elif self._block == "concalls" and label.lower() == "ppt":
                self.links.append(FilingLink("presentation", month, self._href))
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
    try:
        parser.feed(html)
        parser.close()
    except AssertionError:  # html.parser's reaction to some broken markup, e.g. "<![foo["
        return []
    return parser.links


def _month_of(label: str) -> tuple[int, int] | None:
    """(year, month) of an earnings call's "Jul 2026" label; None if it is not one."""
    match = _MONTH_YEAR.match(label)
    if match is None or match.group(1) not in _MONTHS:
        return None
    return int(match.group(2)), _MONTHS[match.group(1)]


def select_filings(links: list[FilingLink], *, today: date, years: int) -> list[FilingLink]:
    """Every official filing of the last ``years`` years, in a fixed order: transcripts,
    presentations, annual reports, announcements (each newest first, as the page lists them).

    Calls and presentations count by the month on their label; an entry without a readable month is
    left out rather than guessed. Annual reports: those among the newest ``years`` listed that are
    on BSE. Announcements: the page shows only the latest few, and all of them are kept.
    """
    oldest = (today.year - years, today.month)
    seen: set[str] = set()
    official = []
    for link in links:
        candidates = file_candidates(link.url)
        if candidates and candidates[0] not in seen:  # kept by its direct file address
            seen.add(candidates[0])
            official.append(FilingLink(link.kind, link.label, candidates[0]))

    def recent(link: FilingLink) -> bool:
        month = _month_of(link.label)
        return month is not None and month >= oldest

    # The newest `years` annual reports as listed, by their direct file address. One on NSE takes
    # its place in the count, so an older BSE report is not pulled in to replace it.
    listed_reports = [link for link in links if link.kind == "annual_report"][:years]
    newest_reports = {
        file_candidates(link.url)[0] for link in listed_reports if file_candidates(link.url)
    }
    chosen = []
    for kind in ("transcript", "presentation", "annual_report", "announcement"):
        for link in official:
            if link.kind != kind:
                continue
            if kind in ("transcript", "presentation") and not recent(link):
                continue
            if kind == "annual_report" and link.url not in newest_reports:
                continue
            chosen.append(link)
    return chosen


def clean_label(label: str) -> str:
    """One line, single spaces, and '' made ' (BSE subjects arrive as "Investors'' Meeting")."""
    return " ".join(label.replace("''", "'").split())[:200]


def title_for(symbol: str, link: FilingLink) -> str:
    return f"{symbol} {KIND_WORDS[link.kind]}, {clean_label(link.label)}"[:200]


def fetch_key(url: str) -> str:
    return "fetch_filing:" + hashlib.sha256(url.encode("utf-8")).hexdigest()


# --- queueing a check: the daily timer, a follow, the "Check for new filings" button -------------

# How long after a check is queued before the same stock may be checked again on demand (a follow
# or the button). Per stock, not per person: it protects screener.in, not a user's quota. The owner
# chose one hour. The daily timer uses the same query with its own interval.
CHECK_COOLDOWN_HOURS = 1

_ENQUEUE_DISCOVERY = text(
    """
    INSERT INTO jobs (kind, payload, dedupe_key)
    SELECT 'discover_filings',
           jsonb_build_object('symbol', s.symbol),
           'discover_filings:' || s.symbol
    FROM stocks s
    WHERE (CAST(:symbol AS text) IS NULL OR s.symbol = :symbol)
      AND NOT EXISTS (
        SELECT 1 FROM jobs j
        WHERE j.dedupe_key = 'discover_filings:' || s.symbol
          AND j.created_at > now() - make_interval(hours => :hours)
    )
    ON CONFLICT (dedupe_key) WHERE status IN ('pending', 'processing') DO NOTHING
    """
)


async def enqueue_discovery(
    db: AsyncSession, *, every_hours: int, symbol: str | None = None
) -> int:
    """Queue a discovery for every stock (or just ``symbol``) not checked within ``every_hours``.

    Returns how many were queued. Two callers racing both pass NOT EXISTS; the partial unique
    index on the live dedupe key then lets only one insert through, and ON CONFLICT skips the rest.
    """
    params = {"hours": every_hours, "symbol": symbol}
    result = cast("CursorResult[Any]", await db.execute(_ENQUEUE_DISCOVERY, params))
    return result.rowcount


@dataclass(frozen=True)
class FilingCheck:
    """What the Documents page shows next to a stock."""

    checking: bool  # a discovery, or a download it queued, is still waiting or running
    last_checked_at: datetime | None  # when the newest completed discovery finished
    next_check_at: datetime | None  # when the button works again; None means now


# The newest discovery of this stock, and whether any of its work is still live. A fetch_filing
# job carries its stock in the payload; a discovery only in its dedupe key.
_CHECK_STATUS = text(
    """
    SELECT
      EXISTS (
        SELECT 1 FROM jobs
        WHERE status IN ('pending', 'processing')
          AND (dedupe_key = 'discover_filings:' || :symbol
               OR (kind = 'fetch_filing' AND payload->>'symbol' = :symbol))
      ) AS checking,
      (SELECT max(updated_at) FROM jobs
        WHERE dedupe_key = 'discover_filings:' || :symbol AND status = 'completed')
        AS last_checked_at,
      (SELECT max(created_at) + make_interval(hours => :hours) FROM jobs
        WHERE dedupe_key = 'discover_filings:' || :symbol) AS cooldown_ends,
      now() AS now
    """
)


async def filing_check(db: AsyncSession, symbol: str) -> FilingCheck:
    row = (await db.execute(_CHECK_STATUS, {"symbol": symbol, "hours": CHECK_COOLDOWN_HOURS})).one()
    ends: datetime | None = row.cooldown_ends
    return FilingCheck(
        checking=row.checking,
        last_checked_at=row.last_checked_at,
        next_check_at=ends if ends is not None and ends > row.now else None,
    )


# --- the two jobs -------------------------------------------------------------------------------

_KNOWN = text("SELECT 1 FROM documents WHERE source_url = ANY(:urls)")

_STOCK = text("SELECT id, is_financial FROM stocks WHERE symbol = :symbol")

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
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    job: ClaimedJob,
    *,
    today: date,
    years: int,
) -> None:
    [symbol] = _payload(job, "symbol")
    async with session_factory() as db:
        stock = (await db.execute(_STOCK, {"symbol": symbol})).one_or_none()
    if stock is None:
        raise JobCannotSucceed(f"no such stock: {symbol[:20]}")

    url = SCREENER_URL.format(symbol=symbol)
    page = (await polite_get(http, url, allowed=is_screener_page(symbol), limit=PAGE_LIMIT)).decode(
        "utf-8", errors="replace"
    )
    links = select_filings(parse_documents(page), today=today, years=years)

    async with session_factory() as db:
        if not await still_mine(db, job):
            return
        # The same page's fundamentals table, as cited facts (ADR 020): no second request.
        await store_screener_facts(
            db, stock_id=stock.id, is_financial=stock.is_financial, page=page, url=url
        )
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
    pause_seconds: float = 2.0,
) -> None:
    symbol, url, kind, label = _payload(job, "symbol", "url", "kind", "label")
    candidates = file_candidates(url)
    if not candidates or kind not in KIND_WORDS:
        raise JobCannotSucceed("not an official filing address")

    async with session_factory() as db:
        stock = await stock_id(db, symbol)
        known = (await db.execute(_KNOWN, {"urls": candidates})).first() is not None
    if stock is None:
        raise JobCannotSucceed(f"no such stock: {symbol[:20]}")

    if not known:
        data, url = await _download(http, candidates, limit=limit, pause_seconds=pause_seconds)
        if not data.startswith(PDF_SIGNATURE):
            raise JobCannotSucceed("the filing address did not return a PDF (not a PDF)")
        pdf = PdfFile(data=data, sha256=hashlib.sha256(data).hexdigest())
        key = blob_key_for(pdf.sha256)
        await store.put(key, pdf.data)  # before the transaction: no transaction spans I/O

    async with session_factory() as db:
        if not await still_mine(db, job):
            return
        if not known:
            await record_document(
                db,
                stock=stock,
                title=title_for(symbol, FilingLink(kind, label, url)),
                pdf=pdf,
                blob_key=key,
                source_url=url,
                kind=kind,
                period=clean_label(label),
            )
        await complete(db, job)
        await db.commit()


async def _download(
    http: httpx.AsyncClient, candidates: list[str], *, limit: int, pause_seconds: float
) -> tuple[bytes, str]:
    """The first candidate address that has the file, and which one it was.

    404, or 503, means "not in this folder": try the next. (Found in the three-year local run: for a
    filing not yet moved to AttachHis, BSE answers 503 there while AttachLive serves it.) If every
    folder said 503, that is an outage: the last 503 propagates and the job queue retries later.
    Only 404 everywhere means the filing is gone. Before each request, a pause, to be gentle with
    BSE.
    """
    unavailable: httpx.HTTPStatusError | None = None
    for candidate in candidates:
        await asyncio.sleep(pause_seconds)
        try:
            return await polite_get(
                http, candidate, allowed=is_official_pdf_url, limit=limit
            ), candidate
        except FetchRefused as error:
            raise JobCannotSucceed(str(error)) from error
        except FetchTooLarge as error:
            raise JobCannotSucceed(f"the filing is larger than {limit} bytes") from error
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 503:
                unavailable = error
            elif error.response.status_code != 404:
                raise
    if unavailable is not None:
        raise unavailable
    raise JobCannotSucceed("the filing was not found at BSE (not found in either folder)")
