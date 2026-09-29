"""Reading RBI's press-release RSS (P15, ADR 007): polite to fetch, lenient to parse, never trusted.

* Only www.rbi.org.in and rbi.org.in are ever fetched or linked to, so a poisoned feed cannot point
  the app anywhere else.
* The feed's text is untrusted: markup is stripped, entities are never expanded by the XML parser
  (a DOCTYPE or ENTITY declaration sends the body to a small reader that uses plain string
  search, so a hostile body costs time in proportion to its size, never more), summaries and
  titles are cut, NUL characters dropped, over-long links skipped, and at most MAX_ITEMS read:
  no single item can make storing the feed fail.
* Real RBI items: title and description in CDATA, the description an HTML table, a link with a
  ``?prid=`` query (kept: it identifies the release), pubDate in RFC 822.
"""

import hashlib
import html
import re
import xml.etree.ElementTree as ET  # DOCTYPE/ENTITY bodies never reach it
from collections.abc import Iterator
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.feeds.model import MAX_SUMMARY_CHARS, RBI_FEED_URL, FeedItem, FetchResult
from app.polite_fetch import polite_fetch

ALLOWED_HOSTS = frozenset({"www.rbi.org.in", "rbi.org.in"})
FEED_LIMIT = 2 * 1024 * 1024
FIXTURE_FILE = Path(__file__).parent / "fixtures" / "rbi_press_releases.xml"
MAX_ITEMS = 200  # RBI's feed carries ten
MAX_TITLE_CHARS = 500  # what feed_items.title allows
MAX_URL_CHARS = 1000  # well under what a unique index can hold

_TRACKING = frozenset({"fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "igshid"})
_DECLARATION = re.compile(rb"<!\s*(doctype|entity)", re.IGNORECASE)
_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-:.")
_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_TAG = re.compile(r"<[^>]*>")
_FIELDS = ("title", "link", "pubDate", "description")


def canonical_url(url: str) -> str | None:
    """https, lower-case host, no fragment, no tracking parameters; None for any other host."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() not in ("http", "https") or host not in ALLOWED_HOSTS:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if port not in (None, 80 if parts.scheme.lower() == "http" else 443):
        return None
    kept = [
        pair for pair in parts.query.split("&") if pair and not _is_tracking(pair.split("=", 1)[0])
    ]
    return urlunsplit(("https", host, parts.path, "&".join(kept), ""))


def _is_tracking(name: str) -> bool:
    name = name.lower()
    return name.startswith("utm_") or name in _TRACKING


def title_hash(title: str) -> str:
    return hashlib.sha256(" ".join(title.lower().split()).encode()).hexdigest()


def _tidy(text: str) -> str:
    """Plain text: tags gone, entities unescaped, whitespace collapsed."""
    return " ".join(html.unescape(_TAG.sub(" ", text)).split())


def _cut(text: str) -> str:
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    head = text[:MAX_SUMMARY_CHARS]
    if not text[MAX_SUMMARY_CHARS].isspace():
        space = head.rfind(" ")
        if space > 0:
            head = head[:space]
    return head.rstrip()


def _date(text: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(text.strip())
    except (ValueError, TypeError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _xml_fields(body: bytes) -> list[dict[str, str]]:
    """Well-formed XML: the parser has already decoded entities and CDATA."""
    root = ET.fromstring(body)  # noqa: S314 (declarations were refused before this)
    return [
        {
            name: "".join(child.itertext())
            for name in _FIELDS
            if (child := el.find(name)) is not None
        }
        for el in root.iter("item")
    ]


def _raw_text(raw: str) -> str:
    """Undo XML escaping by hand: CDATA is kept as written, the rest is unescaped once."""
    pieces = _CDATA.split(raw)  # even positions are outside CDATA, odd ones inside
    return "".join(p if i % 2 else html.unescape(p) for i, p in enumerate(pieces))


def _blocks(text: str, name: str) -> Iterator[str]:
    """What sits inside each <name ...> ... </name>, in turn, found by plain string search. Every
    search starts where the last one ended, so the whole body is read about once: a hostile body
    (openers with no closer, say) costs time in proportion to its size, never more."""
    lower, opener, closer = text.lower(), f"<{name.lower()}", f"</{name.lower()}"
    position = 0
    while (start := lower.find(opener, position)) >= 0:
        after = start + len(opener)
        if after < len(lower) and lower[after] in _NAME_CHARS:  # <items, <item-x: another tag
            position = after
            continue
        opened = lower.find(">", after)
        end = lower.find(closer, opened) if opened >= 0 else -1
        if end < 0:
            return  # nothing complete after this point
        yield text[opened + 1 : end]
        position = end + len(closer)


def _plain_fields(body: bytes) -> list[dict[str, str]]:
    """Broken XML, or a body with declarations: read each <item> by string search. Entities are
    never expanded."""
    text = body.decode("utf-8-sig", errors="replace")
    items = []
    for block in _blocks(text, "item"):
        fields = {}
        for name in _FIELDS:
            found = next(_blocks(block, name), None)
            if found is not None:
                fields[name] = _raw_text(found)
        items.append(fields)
        if len(items) == MAX_ITEMS:
            break
    return items


def parse_feed(body: bytes, *, is_fixture: bool = False) -> list[FeedItem]:
    fields: list[dict[str, str]]
    if _DECLARATION.search(body):
        fields = _plain_fields(body)
    else:
        try:
            fields = _xml_fields(body)[:MAX_ITEMS]
        except (ET.ParseError, ValueError):
            fields = _plain_fields(body)
    items: list[FeedItem] = []
    seen: set[str] = set()
    for f in fields:
        title = _tidy(f.get("title", "").replace("\x00", ""))[:MAX_TITLE_CHARS].rstrip()
        raw_link = f.get("link", "").replace("\x00", "")
        link = canonical_url(raw_link) if len(raw_link) <= MAX_URL_CHARS else None
        if not title or link is None or len(link) > MAX_URL_CHARS or link in seen:
            continue
        seen.add(link)
        items.append(
            FeedItem(
                canonical_url=link,
                title=title,
                title_hash=title_hash(title),
                published_at=_date(f.get("pubDate", "")),
                summary=_cut(_tidy(f.get("description", "").replace("\x00", ""))),
                is_fixture=is_fixture,
            )
        )
    return items


def _is_rbi_https(url: str) -> bool:
    return url.lower().startswith("https://") and canonical_url(url) is not None


async def fetch_feed(
    http: httpx.AsyncClient, *, etag: str | None, last_modified: str | None
) -> FetchResult:
    """A conditional GET. 304 -> not_modified; 200 -> body and new validators; else it raises."""
    conditions = {}
    if etag:
        conditions["If-None-Match"] = etag
    if last_modified:
        conditions["If-Modified-Since"] = last_modified
    got = await polite_fetch(
        http,
        RBI_FEED_URL,
        allowed=_is_rbi_https,
        limit=FEED_LIMIT,
        extra_headers=conditions,
        not_modified_ok=True,
    )
    if got.status == 304:
        return FetchResult("not_modified", b"", etag, last_modified)
    return FetchResult("ok", got.body, got.headers.get("etag"), got.headers.get("last-modified"))


def fixture_items() -> list[FeedItem]:
    """The synthetic items shipped with the app, for FEED_MODE=fixture (offline)."""
    return parse_feed(FIXTURE_FILE.read_bytes(), is_fixture=True)
