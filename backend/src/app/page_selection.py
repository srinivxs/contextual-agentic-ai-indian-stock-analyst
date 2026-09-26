"""Which pages of a filing the extractor LLM reads, and how they are packed into windows (P11).

The three stocks' filings hold about 5,600 pages, but the figures we extract (revenue, profit,
borrowings, equity, dividend, bank ratios) sit on a few hundred of them. Sending every page to the
LLM would cost about $4 and invite mistakes on pages that contain no figures at all (notices,
governance reports, safe-harbour boilerplate). A plain pre-filter picks about 480 pages instead:
about $0.70, and fewer chances for the model to invent a figure. No LLM decides what the LLM reads.

Facts (select_fact_pages):

- annual report: only headline pages, found by their titles ("Consolidated Balance Sheet as at",
  "Statement of Profit and Loss for the year", "Financial Highlights", "Ten-year" summaries,
  "Dividend per share", bank ratios such as "Gross NPA"). At most MAX_ANNUAL_REPORT_PAGES; if more
  match, the pages with the most numbers are kept.
- presentation and transcript: a page qualifies when it has all three of a metric word
  (revenue, profit, debt, EPS, NIM, ...), a money or percent marker (₹, crore, USD, %, ...) and at
  least MIN_NUMBERS numbers. The rupee sign often comes out of the PDF as the letter "H" in front
  of the number ("H12,345 crore"), so a capital H directly before a digit counts as money too.
- announcement or unknown kind: no fact pages (announcements carry events, not the figures).

Events (select_event_pages): the opening pages of a transcript or an announcement, in page order,
while their text fits in EVENT_CHARS (the first page is always read, however long).

Windows (windows): the chosen pages, in page order, packed into texts of at most max_chars, each
page under a "=== PAGE n ===" line so the LLM can say which page a figure is on. A page is never
split across windows; a page too long on its own is cut and ends with "[page truncated]".

Words are matched whole ("patent" is not PAT, "credit" is not Cr) on lower-cased text with runs
of whitespace made single spaces, so a title broken across two lines still matches.
"""

import re
from dataclasses import dataclass

MAX_ANNUAL_REPORT_PAGES = 40
MIN_NUMBERS = 8
EVENT_CHARS = 12_000
PAGE_TRUNCATED = "[page truncated]"

_HEADLINES = re.compile(
    r"(consolidated|standalone) balance sheet as at"
    r"|statement of profit and loss for the (year|period)"
    r"|financial highlights"
    r"|key (financial )?indicators"
    r"|ten[- ]year"
    r"|dividend per (equity )?share"
    r"|net interest margin"
    r"|gross npa"
    r"|key performance"
)
_METRIC_WORDS = re.compile(
    r"\b(revenues?|profits?|pat|borrowings?|debt|equity|net worth|dividends?"
    r"|earnings per share|eps|return on equity|roe|net interest income|net interest margin"
    r"|nim|npas?)\b"
)
_MONEY_WORDS = re.compile(r"₹|%|us\$|\b(rs|inr|crores?|cr|lakhs?|usd|millions?|billions?|bn)\b")
_RUPEE_AS_H = re.compile(r"(?<![A-Za-z])H ?\d")  # on the original text: capital H only
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")  # 12,345 and 12.5 are one number each
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class Window:
    pages: tuple[int, ...]
    text: str


def _normalised(text: str) -> str:
    return _SPACES.sub(" ", text).lower()


def count_numbers(text: str) -> int:
    return len(_NUMBER.findall(text))


def is_headline_page(text: str) -> bool:
    return _HEADLINES.search(_normalised(text)) is not None


def is_metric_page(text: str) -> bool:
    """A metric word, a money or percent marker, and at least MIN_NUMBERS numbers."""
    tidy = _normalised(text)
    has_money = _MONEY_WORDS.search(tidy) is not None or _RUPEE_AS_H.search(text) is not None
    return (
        _METRIC_WORDS.search(tidy) is not None and has_money and count_numbers(text) >= MIN_NUMBERS
    )


def select_fact_pages(kind: str | None, pages: dict[int, str]) -> list[int]:
    if kind == "annual_report":
        matching = [number for number in sorted(pages) if is_headline_page(pages[number])]
        # the most numbers first; on a tie the earlier page (sort is stable)
        matching.sort(key=lambda number: -count_numbers(pages[number]))
        return sorted(matching[:MAX_ANNUAL_REPORT_PAGES])
    if kind in ("presentation", "transcript"):
        return [number for number in sorted(pages) if is_metric_page(pages[number])]
    return []


def select_event_pages(kind: str | None, pages: dict[int, str]) -> list[int]:
    if kind not in ("transcript", "announcement"):
        return []
    chosen: list[int] = []
    used = 0
    for number in sorted(pages):
        size = len(pages[number])
        if chosen and used + size > EVENT_CHARS:
            break
        chosen.append(number)
        used += size
    return chosen


def _page_block(number: int, text: str) -> str:
    return f"=== PAGE {number} ===\n{text}"


def _truncated(block: str, max_chars: int) -> str:
    ending = "\n" + PAGE_TRUNCATED
    return block[: max_chars - len(ending)] + ending


def windows(pages: dict[int, str], selected: list[int], *, max_chars: int = 12_000) -> list[Window]:
    """Pack the selected pages, in page order, into windows of at most max_chars characters."""
    result: list[Window] = []
    group: list[int] = []  # the pages of the window being filled
    text = ""
    for number in sorted(set(selected)):
        block = _page_block(number, pages[number])
        too_long = len(block) > max_chars
        # close the current window if this page goes alone or would not fit (+1 for the newline)
        if group and (too_long or len(text) + 1 + len(block) > max_chars):
            result.append(Window(pages=tuple(group), text=text))
            group, text = [], ""
        if too_long:
            result.append(Window(pages=(number,), text=_truncated(block, max_chars)))
            continue
        text = f"{text}\n{block}" if group else block
        group.append(number)
    if group:
        result.append(Window(pages=tuple(group), text=text))
    return result
