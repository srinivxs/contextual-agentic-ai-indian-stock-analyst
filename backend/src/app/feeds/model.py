"""The shapes of the RBI feed (P15). Every part of P15 agrees on this module.

FEED_MODE=live:    polite, allow-listed, conditional GET of RBI's press-release RSS
FEED_MODE=fixture: the synthetic items shipped in app/feeds/fixtures/ (offline; the default)
    ──> app/feeds/rbi.py: fetch_feed() / parse_feed() / fixture_items() -> FeedItem
    ──> app/feed_jobs.py (the poll_feed job): stored once each, by canonical URL and by title
        hash; an item that names one of the three companies becomes an event for it
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

FEED_SOURCE = "rbi"
RBI_FEED_URL = "https://www.rbi.org.in/pressreleases_rss.xml"
MAX_SUMMARY_CHARS = 600  # kept per item; the UI shows about 300 (ADR 007's display policy)


@dataclass(frozen=True)
class FeedItem:
    canonical_url: str  # https, lower-case host, no fragment, no tracking parameters
    title: str  # tidied, no markup
    title_hash: str  # SHA-256 hex of the title lower-cased with spaces collapsed
    published_at: datetime | None  # UTC; None when the item gives no date
    summary: str  # the description as plain text, cut to MAX_SUMMARY_CHARS
    is_fixture: bool  # True for the synthetic fixture items, shown labelled as such


@dataclass(frozen=True)
class FetchResult:
    status: Literal["ok", "not_modified"]
    body: bytes  # empty when not_modified
    etag: str | None  # to send back as If-None-Match next time
    last_modified: str | None  # to send back as If-Modified-Since next time
