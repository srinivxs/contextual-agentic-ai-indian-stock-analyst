"""The deterministic fact validator (P11): the model proposes a fact, this code decides.

The language model reads a few pages of a filing and proposes candidate facts ("revenue from
operations for FY2026 was 4,12,345 crore, page 4, quote: ..."). Nothing it says is trusted. A fact
is kept only when the page itself proves it, checked in this order (the first failure is the
reason given):

    unknown_metric         the metric is not one of the ten in app/vocabulary.py
    metric_not_applicable  a bank metric for a company, or the reverse
    bad_quote              the quote is empty or longer than 400 characters
    page_outside_window    the cited page is not one of the pages the model was shown
    quote_not_on_page      the quote is not, word for word, on the cited page
    label_not_in_quote     the quote does not name the metric ("net profit", "PAT", ...)
    number_not_in_quote    the value is not one of the numbers in the quote
    ambiguous_currency     rupees or dollars? the quote and page do not say, or say both
    currency_mismatch      the model named the other currency
    unit_not_evidenced     "crore", "%" or "per share" is not shown where it must be
    unparseable_period     the period is not a fiscal year or quarter we can pin down
    period_not_evidenced   the page never mentions that year
    out_of_range           an impossible value (a 150% margin, negative revenue, ...)

Values are stored in one unit per kind: rupee amounts in crore, US-dollar amounts in million (as
reported, never converted), per-share figures in rupees or dollars, ratios in percent.

Page text comes from pypdfium2, which has quirks the comparison must survive: the rupee sign
often comes out as the letter "H" ("H1,234.50 crore"), tables flatten into one line of numbers,
words are split across lines ("borrow-" / "ings"), and quotes and dashes come out curly. The model,
copying a quote, may "fix" those, so both the quote and the page are compared after ``normalise``.

Everything here is a pure function: no I/O, no database, no model.
"""

import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.vocabulary import METRICS, Currency, Metric, Unit, metrics_for, unit_for

MAX_QUOTE_CHARS = 400
REPORTED_TEXT_CHARS = 60

# --- normalising text --------------------------------------------------------------------------

# A soft hyphen marks where a word MAY break; the text on both sides is one word.
_SOFT_HYPHEN = re.compile(r"\N{SOFT HYPHEN}\s*")
# A hyphen at the end of a line followed by a lowercase letter is a word split in two:
# "borrow-\nings" is "borrowings". Before a digit it is kept ("2025-\n26" stays a range).
_SPLIT_WORD = re.compile(r"[-\N{HYPHEN}][ \t]*\r?\n\s*(?=[a-z])")
_CURLY_QUOTES_AND_DASHES = {
    "'": (
        "\N{LEFT SINGLE QUOTATION MARK}",
        "\N{RIGHT SINGLE QUOTATION MARK}",
        "\N{SINGLE LOW-9 QUOTATION MARK}",
        "\N{SINGLE HIGH-REVERSED-9 QUOTATION MARK}",
    ),
    '"': (
        "\N{LEFT DOUBLE QUOTATION MARK}",
        "\N{RIGHT DOUBLE QUOTATION MARK}",
        "\N{DOUBLE LOW-9 QUOTATION MARK}",
    ),
    "-": (
        "\N{HYPHEN}",
        "\N{FIGURE DASH}",
        "\N{EN DASH}",
        "\N{EM DASH}",
        "\N{HORIZONTAL BAR}",
        "\N{MINUS SIGN}",
    ),
}
_STRAIGHTEN = str.maketrans(
    {curly: plain for plain, curlies in _CURLY_QUOTES_AND_DASHES.items() for curly in curlies}
)

# The ways a filing writes "rupees", all treated as one so a quote and its page compare equal:
# the sign, "Rs." or "Rs" as a word, "INR" as a word, and the sign as pypdfium2 often extracts it:
# an "h" starting a word and followed by a digit ("H1,234", "H 2,45,678", "(H1,234)").
# "H1"/"H2" standing alone are the first and second half of a year and are left alone.
# US-dollar markers ("us$", "usd", "$") are kept: they decide the currency.
_RUPEE_MARKER = r"₹|\brs\b\.?|\binr\b|(?<![^\s(])h(?= ?\d)(?![12](?!\d|[,.]\d))"
_RUPEE_MARKER_AND_SPACE = re.compile(rf"(?:{_RUPEE_MARKER})\s*")


def _prepare(text: str) -> str:
    """Lowercase text with the extraction quirks undone, rupee markers still in place."""
    text = unicodedata.normalize("NFKC", text).lower()
    text = _SOFT_HYPHEN.sub("", text)
    text = _SPLIT_WORD.sub("", text)
    return text.translate(_STRAIGHTEN)


def normalise(text: str) -> str:
    """The form in which a quote and its page are compared.

    NFKC (full-width digits, non-breaking spaces), lowercase, soft hyphens and words split across
    a line break joined, curly quotes and dashes made straight, rupee markers removed (with the
    space after them), and all whitespace collapsed to single spaces.
    """
    without_rupees = _RUPEE_MARKER_AND_SPACE.sub("", _prepare(text))
    return " ".join(without_rupees.split())


# --- numbers -----------------------------------------------------------------------------------

# Indian grouping (1,23,45,678: pairs, then a final three), Western grouping (123,456,789), or
# plain digits; then an optional decimal part.
_DIGITS = r"(?:\d{1,3}(?:,\d{2})*,\d{3}|\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_NUMBER_BODY = rf"\((?P<paren>{_DIGITS})\)|(?P<minus>-)?(?P<plain>{_DIGITS})"
# In running text a number must stand on its own: not glued to a word ("fy26", "q3") and not a
# piece of a longer number.
_NUMBER_IN_TEXT = re.compile(rf"(?<![\w.,])(?:{_NUMBER_BODY})(?!\d|[.,]\d)")
_WHOLE_NUMBER = re.compile(_NUMBER_BODY)
_DOLLAR_PREFIX = re.compile(r"^(?:us\$|usd|\$)\s*")


def _to_decimal(match: re.Match[str]) -> Decimal:
    """Brackets mean negative, as in accounts: (2,353) is -2,353."""
    if match.group("paren") is not None:
        return -Decimal(match.group("paren").replace(",", ""))
    value = Decimal(match.group("plain").replace(",", ""))
    return -value if match.group("minus") else value


def parse_number(text: str) -> Decimal | None:
    """One number as printed ("H 2,45,678.9", "(2,353)", "12.5%", "US$ 1.8", "10/-"), or None."""
    cleaned = _DOLLAR_PREFIX.sub("", normalise(text))
    cleaned = cleaned.removesuffix("%").strip().removesuffix("/-").strip()
    match = _WHOLE_NUMBER.fullmatch(cleaned)
    return _to_decimal(match) if match else None


def numbers_in(text: str) -> list[Decimal]:
    """Every number in a text, in order, cleaned the same way as ``parse_number``."""
    return [_to_decimal(match) for match in _NUMBER_IN_TEXT.finditer(normalise(text))]


# --- periods -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Period:
    """An Indian fiscal year or quarter. FY2026 runs from 1 April 2025 to 31 March 2026;
    Q1 is April-June, Q2 July-September, Q3 October-December, Q4 January-March."""

    code: str  # "FY2026" or "Q3FY2026"
    end: date


_MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
_QUARTER_OF_END_MONTH = {6: 1, 9: 2, 12: 3, 3: 4}

# Half years, nine months and year-to-date figures are none of our periods; without this check
# "H1 FY26" would be read as the full year FY26.
_PART_OF_A_YEAR = re.compile(
    r"\bh[12](?!\d)|half[- ]year|\b(?:nine|9) months|\b9mfy|\b9m\b|year to date|\bytd\b"
)
_FY = r"fy\s*'?(\d{4}|\d{2})(?:\s*-\s*(\d{4}|\d{2}))?(?!\d)"  # FY26, FY 2026, FY 2025-26
_QUARTER_AND_YEAR = re.compile(rf"(?:\bq([1-4])|\b([1-4])q)\s*(?:-|of)?\s*{_FY}")  # Q3 FY26, 3QFY26
_ENDED = re.compile(r"\b(quarter|three months|3 months|year|twelve months|12 months) ended\s+")
_FISCAL_YEAR = re.compile(_FY)
_YEAR_RANGE = re.compile(r"(?<!\d)(20\d{2})\s*-\s*(20\d{2}|\d{2})(?!\d)")  # 2025-26
_BARE_QUARTER = re.compile(
    r"\bq([1-4])\b|\b(?:this|the|current|latest) quarter\b|\bquarter under review\b"
)
# Dates as filings write them, with the group numbers of (year, month, day).
_DATE_FORMS = (
    (
        re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]+)\.?,?\s+(\d{4})"),
        (3, 2, 1),
    ),  # 31st March, 2026
    (
        re.compile(r"([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})"),
        (3, 1, 2),
    ),  # March 31, 2026
    (re.compile(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})"), (3, 2, 1)),  # 31.03.2026 (day first)
)


def _fiscal_year_period(fiscal_year: int) -> Period:
    return Period(code=f"FY{fiscal_year}", end=date(fiscal_year, 3, 31))


def _quarter_period(quarter: int, fiscal_year: int) -> Period:
    ends = {
        1: date(fiscal_year - 1, 6, 30),
        2: date(fiscal_year - 1, 9, 30),
        3: date(fiscal_year - 1, 12, 31),
        4: date(fiscal_year, 3, 31),
    }
    return Period(code=f"Q{quarter}FY{fiscal_year}", end=ends[quarter])


def _quarter_number(end_month: int, year: int) -> tuple[int, int]:
    """(quarter, fiscal year) of the quarter ending in this month: June 2026 is (1, 2027)."""
    fiscal_year = year + 1 if end_month >= 4 else year
    return _QUARTER_OF_END_MONTH[end_month], fiscal_year


def _full_year(digits: str) -> int:
    return int(digits) if len(digits) == 4 else 2000 + int(digits)


def _fiscal_year(first: str, second: str | None) -> int | None:
    """The year a fiscal year ends in: "26" -> 2026, "2025"-"26" -> 2026 (one year apart)."""
    start = _full_year(first)
    if second is None:
        return start
    end = _full_year(second)
    return end if end == start + 1 else None


def _month_number(word: str) -> int | None:
    if word.isdigit():
        return int(word)
    matches = [n for n, name in enumerate(_MONTHS, start=1) if name.startswith(word)]
    return matches[0] if len(word) >= 3 and matches else None


def _date_in(text: str, *, at_start: bool) -> date | None:
    """The first real date in the text (or right at its start)."""
    for pattern, (year_group, month_group, day_group) in _DATE_FORMS:
        found = [pattern.match(text)] if at_start else list(pattern.finditer(text))
        for match in found:
            if match is None:
                continue
            month = _month_number(match.group(month_group))
            try:
                return date(int(match.group(year_group)), month or 0, int(match.group(day_group)))
            except ValueError:  # no such month or day
                continue
    return None


def _quarter_ending(day: date) -> Period | None:
    """The quarter ending on this day, if it is the last day of Mar, Jun, Sep or Dec."""
    last_day = calendar.monthrange(day.year, day.month)[1]
    if day.month not in _QUARTER_OF_END_MONTH or day.day != last_day:
        return None
    return _quarter_period(*_quarter_number(day.month, day.year))


def _year_ending(day: date) -> Period | None:
    """The fiscal year ending on this day, if it is 31 March."""
    return _fiscal_year_period(day.year) if (day.month, day.day) == (3, 31) else None


def _bare_quarter(text: str, document_month: date | None) -> Period | None:
    """A bare "Q1" or "this quarter": the latest complete quarter (with that number) before the
    document's month. An earnings call in July 2026 discusses April-June 2026, Q1 of FY2027."""
    match = _BARE_QUARTER.search(text)
    if match is None or document_month is None:
        return None
    end_month, year = (document_month.month - 1) // 3 * 3, document_month.year
    if end_month == 0:  # January to March: the last complete quarter ended in December
        end_month, year = 12, year - 1
    quarter, fiscal_year = _quarter_number(end_month, year)
    wanted = int(match.group(1)) if match.group(1) else quarter
    while quarter != wanted:
        quarter, fiscal_year = (quarter - 1, fiscal_year) if quarter > 1 else (4, fiscal_year - 1)
    return _quarter_period(quarter, fiscal_year)


def parse_period(text: str, *, document_month: date | None) -> Period | None:
    """The fiscal year or quarter a period text names, or None when it cannot be pinned down."""
    text = normalise(text)
    if not text or _PART_OF_A_YEAR.search(text):
        return None
    if match := _QUARTER_AND_YEAR.search(text):
        fiscal_year = _fiscal_year(match.group(3), match.group(4))
        quarter = int(match.group(1) or match.group(2))
        return _quarter_period(quarter, fiscal_year) if fiscal_year else None
    if match := _ENDED.search(text):
        day = _date_in(text[match.end() :], at_start=True)
        if day is None:
            return None
        is_quarter = match.group(1) in ("quarter", "three months", "3 months")
        return _quarter_ending(day) if is_quarter else _year_ending(day)
    if match := _FISCAL_YEAR.search(text) or _YEAR_RANGE.search(text):
        fiscal_year = _fiscal_year(match.group(1), match.group(2))
        return _fiscal_year_period(fiscal_year) if fiscal_year else None
    if day := _date_in(text, at_start=False):
        return _year_ending(day)  # a bare date names a period only when it is a year end
    return _bare_quarter(text, document_month)


def _period_evidenced(period: Period, page: str) -> bool:
    """Does the (normalised) page mention the period's year?

    The fiscal year counts in any usual form ("2026", "FY26", "FY'26", "Q3FY26", "2025-26",
    "FY 25-26"); for a quarter, so does the calendar year it ends in ("December 31, 2025").
    A bare "26" does not: it is as likely a page or note number.
    """
    fiscal_year = int(period.code[-4:])
    yy, previous_yy = f"{fiscal_year % 100:02d}", f"{(fiscal_year - 1) % 100:02d}"
    patterns = [
        rf"(?<![\d,.]){fiscal_year}(?!\d)",
        rf"fy\s*'?(?:20)?{yy}(?!\d)",
        rf"fy\s*'?(?:20)?{previous_yy}\s*-\s*(?:20)?{yy}(?!\d)",
        rf"(?<!\d)20{previous_yy}\s*-\s*(?:20)?{yy}(?!\d)",
    ]
    if period.code.startswith("Q"):
        patterns.append(rf"(?<![\d,.]){period.end.year}(?!\d)")
    return any(re.search(pattern, page) for pattern in patterns)


# --- currency ----------------------------------------------------------------------------------

_RUPEE_MARKER_ONLY = re.compile(_RUPEE_MARKER)
_USD_WORD = re.compile(r"\busd\b")
# Scale words that exist only in rupees; they are written AFTER the number.
_RUPEE_SCALE = re.compile(r"\b(?:crores?|cr|lakhs?|lacs?)\b")


def _marked(text: str) -> str:
    """The prepared text with every rupee marker written "₹" and "usd" written "$"."""
    return _USD_WORD.sub("$", _RUPEE_MARKER_ONLY.sub("₹", _prepare(text)))


def _currencies_anywhere(marked: str) -> set[Currency]:
    found: set[Currency] = set()
    if "₹" in marked or _RUPEE_SCALE.search(marked):
        found.add("INR")
    if "$" in marked:
        found.add("USD")
    return found


def _currencies_next_to(value: Decimal, marked: str) -> set[Currency]:
    """The currency written next to the value itself.

    A currency sign is written before its number, so a sign between the previous number and this
    one belongs to this one; "crore" and "lakh" are written after, so they belong to the number
    before them. In "₹15,000 crore (US$ 1.8 billion)" that gives 15,000 rupees and 1.8 dollars.
    """
    numbers = list(_NUMBER_IN_TEXT.finditer(marked))
    found: set[Currency] = set()
    for i, number in enumerate(numbers):
        if _to_decimal(number) != value:
            continue
        before = marked[numbers[i - 1].end() if i > 0 else 0 : number.start()]
        after = marked[number.end() : numbers[i + 1].start() if i + 1 < len(numbers) else None]
        if "₹" in before or _RUPEE_SCALE.search(after):
            found.add("INR")
        if "$" in before:
            found.add("USD")
    return found


def _evidenced_currency(value: Decimal, quote: str, page: str) -> Currency | None:
    """Rupees or dollars, from the closest evidence that says: next to the value, elsewhere in
    the quote, then anywhere on the page. None when the closest evidence says both or nothing."""
    marked_quote = _marked(quote)
    for found in (_currencies_next_to(value, marked_quote), _currencies_anywhere(marked_quote)):
        if found:
            return found.pop() if len(found) == 1 else None
    on_page = _currencies_anywhere(_marked(page))
    return on_page.pop() if len(on_page) == 1 else None


# --- units and scale ---------------------------------------------------------------------------

_SCALE_WORDS = {
    "crore": re.compile(r"\b(?:crores?|cr)\b"),
    "lakh": re.compile(r"\b(?:lakhs?|lacs?)\b"),
    "million": re.compile(r"\b(?:millions?|mn|mln)\b"),
    "billion": re.compile(r"\b(?:billions?|bn)\b"),
    "thousand": re.compile(r"\bthousands?\b"),
}
_TO_CRORE = {
    "lakh": Decimal("0.01"),
    "crore": Decimal(1),
    "million": Decimal("0.1"),
    "billion": Decimal(100),
    "thousand": Decimal("0.0001"),
}
_TO_MILLION = {"thousand": Decimal("0.001"), "million": Decimal(1), "billion": Decimal(1000)}
_PERCENT_SIGN = re.compile(r"%|\bper ?cent\b")
_PER_SHARE_WORDING = re.compile(r"\bper (?:equity |ordinary )?share\b|/ ?share\b|\beps\b|\bdps\b")


def _in_canonical_unit(
    metric: Metric, unit_word: str, value: Decimal, currency: Currency | None, quote: str, page: str
) -> tuple[Decimal, Unit] | None:
    """The value converted to its canonical unit, or None when the unit is not shown.

    A percentage needs "%" or "per cent" in the quote; a per-share figure needs "per share" (or
    EPS/DPS) in the quote and no scale word; an amount needs its scale word ("crore", "mn", ...)
    on the page, often only in a table heading such as "(₹ in crore)". Dollars have no crore or
    lakh.
    """
    unit = unit_for(metric.kind, currency)
    if metric.kind == "percent":
        shown = unit_word in ("percent", "none") and _PERCENT_SIGN.search(quote) is not None
        return (value, unit) if shown else None
    if metric.kind == "per_share":
        shown = unit_word in ("per_share", "none") and _PER_SHARE_WORDING.search(quote) is not None
        return (value, unit) if shown else None
    factor = (_TO_CRORE if unit == "INR_CRORE" else _TO_MILLION).get(unit_word)
    if factor is None or not _SCALE_WORDS[unit_word].search(page):
        return None
    return value * factor, unit


def _in_range(metric: Metric, value: Decimal, unit: Unit) -> bool:
    """Values no real filing could contain. Only net profit and EPS (a loss) and return on equity
    may be negative."""
    if unit == "PERCENT":
        lowest = -100 if metric.name == "return_on_equity" else 0
        return lowest <= value <= 100
    if unit in ("INR_PER_SHARE", "USD_PER_SHARE"):
        if metric.name == "eps_basic":
            return value != 0 and abs(value) < 10_000
        return 0 < value < 10_000
    if value < 0 and metric.name != "net_profit":
        return False
    limit = Decimal("1e8") if unit == "INR_CRORE" else Decimal("1e7")
    return 0 < abs(value) < limit


def _basis_on_page(claimed: str, page: str) -> str:
    """A consolidated or standalone claim is kept only when the page says so."""
    if claimed in ("consolidated", "standalone") and re.search(rf"\b{claimed}\b", page):
        return claimed
    return "unspecified"


# --- the validator -----------------------------------------------------------------------------

# One regex per metric: any of its synonyms, as whole words.
_LABELS = {
    name: re.compile(r"\b(?:" + "|".join(metric.synonyms) + r")\b")
    for name, metric in METRICS.items()
}


# Definition guards (the owner's option A after the hand check of the first real run): the quote
# names the metric, but its figure is a different number from the one the metric means.
_ADJUSTED = re.compile(
    r"\b(?:excluding|before) exceptional|\badjusted\b|\bunderlying\b|\bnormali[sz]ed\b"
)
_INSTALMENT = re.compile(r"\b(?:final|interim|special) dividend")
_TOTAL_DIVIDEND = re.compile(r"\btotal dividend")
_PAID = re.compile(r"\bdividends? (?:on equity shares|paid)\b|\bappropriation")


def definition_problem(metric: str, quote: str) -> str | None:
    """Why the quote's figure is not the metric's figure, or None when it may be.

    - net profit is the reported figure, not one excluding exceptional items or "adjusted";
    - a dividend per share is the year's total, not a final, interim or special instalment alone,
      and not what was paid or appropriated during the year (that is the year before's dividend).
    """
    text = normalise(quote)
    if metric == "net_profit" and _ADJUSTED.search(text):
        return "adjusted_figure"
    if metric == "dividend_per_share":
        if _PAID.search(text):
            return "dividend_paid_not_declared"
        if _INSTALMENT.search(text) and not _TOTAL_DIVIDEND.search(text):
            return "partial_dividend"
    return None


def label_in_quote(metric: str, quote: str) -> bool:
    """Whether the quote names the metric, by the current synonyms (also used to re-check facts
    already stored when a rule is tightened: app/recheck_facts.py)."""
    label = _LABELS.get(metric)
    return label is not None and label.search(normalise(quote)) is not None


@dataclass(frozen=True)
class Candidate:
    """A fact as the model proposes it: plain strings, exactly as the model returned them."""

    metric: str
    value_text: str  # as printed: "4,12,345", "(2,353)", "18.5"
    unit_word: str  # crore, lakh, million, billion, thousand, percent, per_share or none
    currency: str  # INR, USD or none
    period_text: str  # "FY26", "Q3 FY2026", "quarter ended December 31, 2025", ...
    basis: str  # consolidated, standalone or unspecified
    page: int
    quote: str


@dataclass(frozen=True)
class AcceptedFact:
    metric: str
    period: Period
    basis: str
    value: Decimal  # in ``unit``
    unit: Unit
    currency: Currency | None  # None for a percentage
    reported_text: str  # the value as printed, at most 60 characters
    page: int
    quote: str


@dataclass(frozen=True)
class Rejection:
    code: str
    metric: str
    page: int


def _reject(candidate: Candidate, code: str) -> Rejection:
    return Rejection(code=code, metric=candidate.metric, page=candidate.page)


def validate(
    candidate: Candidate,
    *,
    pages: dict[int, str],
    is_financial: bool,
    document_month: date | None,
) -> AcceptedFact | Rejection:
    """Accept the candidate only if the cited page proves it; otherwise say why not.

    ``pages`` holds the text of the pages the model was shown (page number -> text);
    ``is_financial`` is true for a bank; ``document_month`` dates the filing, for a bare "Q1".
    """
    metric = METRICS.get(candidate.metric)
    if metric is None:
        return _reject(candidate, "unknown_metric")
    if metric not in metrics_for(is_financial):
        return _reject(candidate, "metric_not_applicable")

    quote = normalise(candidate.quote)
    if not quote or len(candidate.quote) > MAX_QUOTE_CHARS:
        return _reject(candidate, "bad_quote")
    if candidate.page not in pages:
        return _reject(candidate, "page_outside_window")
    page = normalise(pages[candidate.page])
    if quote not in page:
        return _reject(candidate, "quote_not_on_page")
    if not _LABELS[metric.name].search(quote):
        return _reject(candidate, "label_not_in_quote")
    problem = definition_problem(metric.name, candidate.quote)
    if problem is not None:
        return _reject(candidate, problem)
    value = parse_number(candidate.value_text)
    if value is None or value not in numbers_in(candidate.quote):
        return _reject(candidate, "number_not_in_quote")

    currency: Currency | None = None
    if metric.kind != "percent":
        currency = _evidenced_currency(value, candidate.quote, pages[candidate.page])
        if currency is None:
            return _reject(candidate, "ambiguous_currency")
        if candidate.currency not in ("none", currency):
            return _reject(candidate, "currency_mismatch")
    converted = _in_canonical_unit(metric, candidate.unit_word, value, currency, quote, page)
    if converted is None:
        return _reject(candidate, "unit_not_evidenced")
    value, unit = converted

    period = parse_period(candidate.period_text, document_month=document_month)
    if period is None:
        return _reject(candidate, "unparseable_period")
    if not _period_evidenced(period, page):
        return _reject(candidate, "period_not_evidenced")
    if not _in_range(metric, value, unit):
        return _reject(candidate, "out_of_range")

    return AcceptedFact(
        metric=metric.name,
        period=period,
        basis=_basis_on_page(candidate.basis, page),
        value=value,
        unit=unit,
        currency=currency,
        reported_text=candidate.value_text.strip()[:REPORTED_TEXT_CHARS],
        page=candidate.page,
        quote=candidate.quote,
    )
