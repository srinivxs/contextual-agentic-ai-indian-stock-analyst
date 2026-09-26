"""The numbers on a stock's screener.in company page, read deterministically (P11, the owner's
decision; amends ADR 018, which read only the filing links on that page).

    company page HTML ──> parse_fundamentals ──> ScreenerFigure (section, row, column, value)
                                              ──> map_to_vocabulary ──> MappedFact (metric, period)
                      ──> parse_top_ratios   ──> TopRatios (market cap, price, P/E, ...)

WHAT IS READ: only the page the worker already fetches once a day for filing links,
https://www.screener.in/company/<SYMBOL>/consolidated/ (``filings.SCREENER_URL``). From it, the
five data tables (quarterly results, profit and loss, balance sheet, cash flows, ratios) and the
"top ratios" list at the top. Nothing else is requested for this.

WHAT IS CITED: every figure keeps the section, the row label and the column label it came from
("profit-loss", "Net Profit", "Mar 2026"); with the page's URL that is the citation, so anyone can
open the page and find the cell. Money in those tables is in ₹ crore: each money table says
"Figures in Rs. Crores" in its caption, and a table that does not say so is not read at all, rather
than guessing its unit.

WHAT IS NEVER READ: anything behind screener's login (its "insights" table is blurred for visitors;
we never log in), and the "Raw PDF" links of the quarterly table, which point at
/company/source/quarter/*, a path screener's robots.txt disallows. That row is dropped unread.

No LLM is involved: the HTML is parsed with the standard library and the mapping from a row label
to our metric name is the fixed table below. A layout change finds nothing; it never raises.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from html.parser import HTMLParser
from typing import Literal

SECTIONS = ("quarters", "profit-loss", "balance-sheet", "cash-flow", "ratios")
# The four tables whose amounts are money; "ratios" holds days and percentages only.
_MONEY_SECTIONS = frozenset({"quarters", "profit-loss", "balance-sheet", "cash-flow"})
UNITS_CAPTION = "Figures in Rs. Crores"

Basis = Literal["consolidated", "standalone"]
Unit = Literal["INR_CRORE", "INR_PER_SHARE", "PERCENT"]


@dataclass(frozen=True)
class ScreenerFigure:
    """One cell of one of the five tables, and where it is on the page."""

    section: str  # "quarters", "profit-loss", "balance-sheet", "cash-flow" or "ratios"
    row_label: str  # "Net Profit" (the page's "Net Profit +" without the expander "+")
    column_label: str  # "Mar 2026", "Dec 2025", "TTM"
    value: Decimal | None  # None for an empty cell (never read as zero)


@dataclass(frozen=True)
class TopRatios:
    """The list at the top of the page. None when the item is missing or has no single number."""

    market_cap_crore: Decimal | None
    current_price: Decimal | None
    stock_pe: Decimal | None
    book_value: Decimal | None
    dividend_yield_percent: Decimal | None
    roce_percent: Decimal | None
    roe_percent: Decimal | None
    face_value: Decimal | None


@dataclass(frozen=True)
class MappedFact:
    """A figure under our own metric name and fiscal period, still carrying its citation."""

    metric: str  # "net_profit", "total_equity", ...
    period: str  # "FY2026" or "Q3FY2026"
    period_end: date
    basis: Basis  # which view of the accounts the page showed
    value: Decimal
    unit: Unit
    currency: Literal["INR"] | None  # None for a percentage
    section: str  # the citation: which table,
    row_label: str  # which row ("Equity Capital + Reserves" for the one computed fact),
    column_label: str  # and which column


# --- reading numbers -----------------------------------------------------------------------------

# Digits in groups separated by single commas (Western "1,234,567" or Indian "12,34,567"), an
# optional minus and decimals. Nothing else: not "NaN", not "1e5", not "--".
_PLAIN_NUMBER = re.compile(r"^-?\d+(?:,\d+)*(?:\.\d+)?$")


def _number(text: str) -> Decimal | None:
    """ "1,234" -> 1234, "-12" -> -12, "18%" -> 18, "" -> None, anything else -> None."""
    text = text.strip().removesuffix("%").strip()
    if not _PLAIN_NUMBER.match(text):
        return None
    return Decimal(text.replace(",", ""))


def _one_line(parts: list[str]) -> str:
    """Joined text with every run of whitespace (a non-breaking space too) as one space."""
    return " ".join("".join(parts).split())


def _clean_label(text: str) -> str:
    """ "Sales +" -> "Sales": the "+" is the page's button that expands a row, not its name."""
    return text.removesuffix("+").strip()


def _feed(parser: HTMLParser, html: str) -> bool:
    """Parse the whole page; False if html.parser gave up on it."""
    try:
        parser.feed(html)
        parser.close()
    except AssertionError:  # html.parser's reaction to some broken markup, e.g. "<![foo["
        return False
    return True


# --- the five tables -----------------------------------------------------------------------------


class _TablesParser(HTMLParser):
    """Reads the table with class "data-table" in each of the five sections.

    A table's first row (its <th> cells) gives the column labels; every later row is a label cell
    followed by one cell per column. A section's figures are kept only when the section ends, and
    a money section only if its caption states the unit.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.figures: list[ScreenerFigure] = []
        self._section: str | None = None  # the wanted section we are in
        self._pending: list[ScreenerFigure] = []  # its figures, until it ends
        self._caption: list[str] = []  # its text outside the data table
        self._table_depth = 0  # 1 inside the data table; more inside a table nested in it
        self._columns: list[str] = []
        self._row: list[str] | None = None
        self._row_is_header = False
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "section":
            section = attributes.get("id")
            self._start_section(section if section in SECTIONS else None)
        elif self._section is None:
            return
        elif tag == "table":
            if self._table_depth > 0:
                self._table_depth += 1
            elif "data-table" in (attributes.get("class") or "").split():
                self._table_depth, self._columns = 1, []
        elif self._table_depth != 1:
            return
        elif tag == "tr":
            self._row, self._row_is_header = [], False
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._row_is_header = self._row_is_header or tag == "th"

    def handle_endtag(self, tag: str) -> None:
        if tag == "section":
            self._end_section()
        elif tag == "table" and self._table_depth > 0:
            self._table_depth -= 1
            if self._table_depth == 0:
                self._row = self._cell = None
        elif self._table_depth != 1:
            return
        elif tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(_one_line(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._end_row(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._section is None:
            return
        if self._table_depth == 0:
            self._caption.append(data)
        elif self._table_depth == 1 and self._cell is not None:
            self._cell.append(data)

    def _start_section(self, section: str | None) -> None:
        self._section, self._pending, self._caption = section, [], []
        self._table_depth, self._row, self._cell = 0, None, None

    def _end_section(self) -> None:
        states_unit = UNITS_CAPTION in _one_line(self._caption)
        if self._section not in _MONEY_SECTIONS or states_unit:
            self.figures.extend(self._pending)
        self._start_section(None)

    def _end_row(self, row: list[str]) -> None:
        if not row or self._section is None:
            return
        if self._row_is_header:
            self._columns = row[1:]  # the first header cell is the empty corner
            return
        label = _clean_label(row[0])
        if label in ("", "Raw PDF"):  # nothing to cite, or links we never follow
            return
        for column, cell in zip(self._columns, row[1:], strict=False):  # extra cells: no column
            self._pending.append(ScreenerFigure(self._section, label, column, _number(cell)))


def parse_fundamentals(html: str) -> list[ScreenerFigure]:
    """Every cell of the five tables, in page order. A page that cannot be read gives []."""
    parser = _TablesParser()
    return parser.figures if _feed(parser, html) else []


# --- the top ratios ------------------------------------------------------------------------------


class _TopRatiosParser(HTMLParser):
    """Reads ul#top-ratios: each <li> has a span.name and a value holding span.number(s).

    For each item it keeps the name, the text of each number span, and the value's whole text
    (to check a unit such as "Cr.").
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: dict[str, tuple[list[str], str]] = {}  # name -> (numbers, value text)
        self._in_list = False
        self._name: list[str] | None = None  # the <li> being read
        self._numbers: list[list[str]] = []
        self._value: list[str] = []
        self._spans: list[str] = []  # the open <span>s: "name", "number" or ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "ul" and attributes.get("id") == "top-ratios":
            self._in_list = True
        elif not self._in_list:
            return
        elif tag == "li":
            self._name, self._numbers, self._value, self._spans = [], [], [], []
        elif tag == "span" and self._name is not None:
            classes = (attributes.get("class") or "").split()
            role = "name" if "name" in classes else "number" if "number" in classes else ""
            self._spans.append(role)
            if role == "number":
                self._numbers.append([])

    def handle_endtag(self, tag: str) -> None:
        if tag == "ul" and self._in_list:
            self._in_list = False
        elif tag == "span" and self._spans:
            self._spans.pop()
        elif tag == "li" and self._name is not None:
            numbers = [_one_line(number) for number in self._numbers]
            self.items[_one_line(self._name)] = (numbers, _one_line(self._value))
            self._name = None

    def handle_data(self, data: str) -> None:
        if self._name is None:
            return
        if "name" in self._spans:
            self._name.append(data)
            return
        self._value.append(data)
        if self._spans and self._spans[-1] == "number":
            self._numbers[-1].append(data)


def _ratio(items: dict[str, tuple[list[str], str]], name: str, *, says: str = "") -> Decimal | None:
    """The item's number, if it has exactly one and its text contains ``says`` (a unit)."""
    numbers, text = items.get(name, ([], ""))
    if len(numbers) != 1 or says not in text:
        return None  # missing, or a pair such as "High / Low"
    return _number(numbers[0])


def parse_top_ratios(html: str) -> TopRatios:
    parser = _TopRatiosParser()
    items = parser.items if _feed(parser, html) else {}
    return TopRatios(
        market_cap_crore=_ratio(items, "Market Cap", says="Cr."),
        current_price=_ratio(items, "Current Price"),
        stock_pe=_ratio(items, "Stock P/E"),
        book_value=_ratio(items, "Book Value"),
        dividend_yield_percent=_ratio(items, "Dividend Yield"),
        roce_percent=_ratio(items, "ROCE"),
        roe_percent=_ratio(items, "ROE"),
        face_value=_ratio(items, "Face Value"),
    )


# --- mapping to our metric names -----------------------------------------------------------------

# (section, row label) -> (metric, unit). These rows mean the same for every company.
_EVERY_COMPANY: dict[tuple[str, str], tuple[str, Unit]] = {
    ("quarters", "Net Profit"): ("net_profit", "INR_CRORE"),
    ("quarters", "EPS in Rs"): ("eps_basic", "INR_PER_SHARE"),
    ("profit-loss", "Net Profit"): ("net_profit", "INR_CRORE"),
    ("profit-loss", "EPS in Rs"): ("eps_basic", "INR_PER_SHARE"),
    ("ratios", "ROE %"): ("return_on_equity", "PERCENT"),  # never ROCE: a different ratio
}

# Not for a bank: a bank's "Revenue" is mostly interest earned, and its borrowings are its raw
# material, not debt in the debt-to-equity sense (which does not apply to banks, ADR 009).
_NOT_FOR_BANKS: dict[tuple[str, str], tuple[str, Unit]] = {
    ("quarters", "Sales"): ("revenue_from_operations", "INR_CRORE"),
    ("quarters", "Revenue"): ("revenue_from_operations", "INR_CRORE"),
    ("profit-loss", "Sales"): ("revenue_from_operations", "INR_CRORE"),
    ("profit-loss", "Revenue"): ("revenue_from_operations", "INR_CRORE"),
    ("balance-sheet", "Borrowings"): ("total_borrowings", "INR_CRORE"),
}

_MONTH_YEAR = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4})$")
_MONTHS = {name: number for number, name in enumerate(calendar.month_abbr) if name}
# The Indian fiscal year runs April to March: the quarter ending in June is Q1.
_QUARTER_ENDING = {6: 1, 9: 2, 12: 3, 3: 4}


def _period(section: str, column: str) -> tuple[str, date] | None:
    """ "Mar 2026" in an annual table -> ("FY2026", 31 Mar 2026); "Dec 2025" in the quarterly
    table -> ("Q3FY2026", 31 Dec 2025). None for "TTM" and for a month that is not a fiscal
    year end (annual) or a quarter end (quarterly): skipped, never guessed."""
    match = _MONTH_YEAR.match(column)
    if match is None:
        return None
    month, year = _MONTHS[match.group(1)], int(match.group(2))
    if section != "quarters":
        return (f"FY{year}", date(year, 3, 31)) if month == 3 else None
    if month not in _QUARTER_ENDING:
        return None
    fiscal_year = year if month == 3 else year + 1
    last_day = calendar.monthrange(year, month)[1]
    return f"Q{_QUARTER_ENDING[month]}FY{fiscal_year}", date(year, month, last_day)


def _fact(
    metric: str,
    unit: Unit,
    period: tuple[str, date],
    value: Decimal,
    view: Basis,
    *,
    section: str,
    row_label: str,
    column_label: str,
) -> MappedFact:
    return MappedFact(
        metric=metric,
        period=period[0],
        period_end=period[1],
        basis=view,
        value=value,
        unit=unit,
        currency=None if unit == "PERCENT" else "INR",
        section=section,
        row_label=row_label,
        column_label=column_label,
    )


def _total_equity(figures: list[ScreenerFigure], view: Basis) -> list[MappedFact]:
    """Equity = Equity Capital + Reserves of the same balance-sheet column. The page has no total
    equity row; the sum is done here, in code. A column missing either part gives nothing."""

    def row(label: str) -> dict[str, Decimal | None]:
        return {
            f.column_label: f.value
            for f in figures
            if f.section == "balance-sheet" and f.row_label == label
        }

    capital, reserves = row("Equity Capital"), row("Reserves")
    facts = []
    for column, capital_value in capital.items():
        reserves_value = reserves.get(column)
        period = _period("balance-sheet", column)
        if capital_value is None or reserves_value is None or period is None:
            continue
        facts.append(
            _fact(
                "total_equity",
                "INR_CRORE",
                period,
                capital_value + reserves_value,
                view,
                section="balance-sheet",
                row_label="Equity Capital + Reserves",
                column_label=column,
            )
        )
    return facts


def map_to_vocabulary(
    figures: list[ScreenerFigure], *, is_financial: bool, view: Basis
) -> list[MappedFact]:
    """Our metrics from the page's figures: the fixed table above in page order, then equity.

    ``is_financial`` (a bank) keeps only net profit, EPS, equity and ROE. ``view`` is the view the
    page showed ("consolidated" for the /consolidated/ page) and becomes each fact's basis.
    """
    rows = _EVERY_COMPANY if is_financial else _EVERY_COMPANY | _NOT_FOR_BANKS
    facts = []
    for figure in figures:
        mapping = rows.get((figure.section, figure.row_label))
        period = _period(figure.section, figure.column_label)
        if mapping is None or period is None or figure.value is None:
            continue
        metric, unit = mapping
        facts.append(
            _fact(
                metric,
                unit,
                period,
                figure.value,
                view,
                section=figure.section,
                row_label=figure.row_label,
                column_label=figure.column_label,
            )
        )
    return facts + _total_equity(figures, view)
