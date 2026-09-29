"""What the stock page shows, computed on read from stored facts and events (P11c, ADR 020).

Everything here is a pure function over rows the API has already loaded, so it is tested without a
database. The choices are app/derived.py's (the owner's conflict policy, ADR 009); this module only
decides what to show and attaches a citation to every figure:

- key facts: per metric one view (consolidated ₹ first, then standalone, then basis not stated; a
  US$ figure only when no ₹ one exists, shown as reported), the latest three fiscal years;
- derived values: debt to equity, growth of revenue (net interest income for a bank) and of net
  profit, the latest dividend, each citing the facts it used;
- recent events, newest first.

The chat (the owner's review, 2026-09-29) reads one measure for one answer with measure_facts:
all the years it needs come from ONE source, so that the answer, its changes and its table never
show two figures for one thing:

- the years needed are the ones the question names, else the latest ``years`` full years;
- the source is the best-ranked one (SOURCE_RANK: annual report first) that has every one of
  those years; only if none has them all does each year take its own best figure;
- another source's figure for the same year that differs by more than 1% is kept as a rival,
  so the answer can say so instead of choosing silently;
- change_views computes the change between consecutive years of that one source (never across
  two sources: that would measure a change of definition, ADR 020), and its difference.

What a source calls a figure (``reported_as``, the owner's second review): screener.in's row
("Sales", "Net Profit") or a filing's own line, read from the stored quote up to its first number
("Revenue from Operations 25 9,80,136" -> "Revenue from Operations"). Two sources that disagree
are then told apart by their own words, never assumed to measure the same thing.
"""

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise
from typing import Literal

from app.derived import (
    SOURCE_RANK,
    SOURCE_WORDS,
    Chosen,
    Derived,
    EventRow,
    FactRow,
    agrees,
    choose_all,
    debt_to_equity,
    growth_yoy,
    latest_dividend,
)
from app.vocabulary import METRICS

YEARS_SHOWN = 3
EVENTS_SHOWN = 10

METRIC_LABELS = {
    "revenue_from_operations": "Revenue from operations",
    "net_interest_income": "Net interest income",
    "net_profit": "Net profit",
    "total_borrowings": "Total borrowings",
    "total_equity": "Total equity",
    "dividend_per_share": "Dividend per share",
    "eps_basic": "Basic EPS",
    "return_on_equity": "Return on equity",
    "net_interest_margin": "Net interest margin",
    "gross_npa_ratio": "Gross NPA ratio",
}

KIND_NAMES = {
    "annual_report": "Annual report",
    "presentation": "Investor presentation",
    "transcript": "Earnings call",
    "announcement": "Announcement",
}

# Which view of a metric the page shows: the first of these that has a full-year figure.
_VIEWS = [
    (basis, currency)
    for currency in ("INR", "USD", None)
    for basis in ("consolidated", "standalone", "unspecified")
]


@dataclass(frozen=True)
class Citation:
    source: Literal["filing", "screener", "rbi"]  # rbi: an RBI press release (P15)
    label: str
    url: str | None
    quote: str | None


@dataclass(frozen=True)
class StoredFact:
    row: FactRow
    citation: Citation
    reported_as: str | None = None  # what the source calls it: "Sales", "Revenue from Operations"


@dataclass(frozen=True)
class StoredEvent:
    row: EventRow
    summary: str
    citation: Citation


@dataclass(frozen=True)
class Rival:
    """Another source's figure for the same metric, period, basis and currency, more than 1%
    away from the one shown."""

    citation: Citation
    value: Decimal
    unit: str
    reported_as: str | None = None


@dataclass(frozen=True)
class KeyFact:
    metric: str
    label: str
    period: str
    basis: str
    currency: str | None
    unit: str
    value: Decimal
    status: str
    corroborated_by: int
    citation: Citation
    disputed_by: tuple[Citation, ...]
    rivals: tuple[Rival, ...] = ()
    row: FactRow | None = None  # the stored row, for computing changes
    reported_as: str | None = None  # what its source calls it


@dataclass(frozen=True)
class DerivedView:
    name: str
    label: str
    status: str
    value: Decimal | None
    reason: str
    citations: tuple[Citation, ...]
    inputs: tuple[FactRow, ...] = ()  # the stored figures it was computed from
    difference: Decimal | None = None  # a change: the later figure minus the earlier one


_FIRST_NUMBER = re.compile(r"[\d₹(]")
# Left over at the end of a label: a mis-read rupee sign ("PAT at H 95,754"), a currency word,
# or a word that only leads into the number.
_LABEL_TAIL = {"rs", "rs.", "inr", "at", "of", "was", "is", "to", "were", "stood", "by"}


def reported_label(quote: str | None) -> str | None:
    """A filing's own name for a figure: its quote up to the first number, or None."""
    if not quote:
        return None
    words = _FIRST_NUMBER.split(quote, maxsplit=1)[0].split()
    while words and (len(words[-1]) <= 1 or words[-1].lower() in _LABEL_TAIL):
        words.pop()
    label = " ".join(words).strip(" :-,.")
    return label[:80] or None


def filing_citation(
    kind: str | None,
    period: str | None,
    title: str,
    source_url: str | None,
    page: int,
    quote: str,
) -> Citation:
    """ "Earnings call · Jul 2026 · p.7", linking to that page of the official PDF."""
    name = f"{KIND_NAMES[kind]} · {period}" if kind in KIND_NAMES and period else title
    return Citation(
        source="filing",
        label=f"{name} · p.{page}",
        url=f"{source_url}#page={page}" if source_url else None,
        quote=quote,
    )


def screener_citation(url: str, section: str, row: str, column: str) -> Citation:
    """The fundamentals row: "screener.in · profit-loss · Net Profit · Mar 2026"."""
    return Citation(
        source="screener",
        label=f"screener.in · {section} · {row} · {column}",
        url=url,
        quote=None,
    )


def _key_fact(chosen: Chosen, citations: dict[int, Citation]) -> KeyFact:
    fact = chosen.fact
    return KeyFact(
        metric=fact.metric,
        label=METRIC_LABELS[fact.metric],
        period=fact.period,
        basis=fact.basis,
        currency=fact.currency,
        unit=fact.unit,
        value=fact.value,
        status=chosen.status,
        corroborated_by=chosen.corroborated_by,
        citation=citations[fact.id],
        disputed_by=tuple(citations[other.id] for other in chosen.disagreeing),
    )


def key_facts(facts: list[StoredFact], *, years: int = YEARS_SHOWN) -> list[KeyFact]:
    citations = {stored.row.id: stored.citation for stored in facts}
    chosen = choose_all([stored.row for stored in facts])
    shown: list[KeyFact] = []
    for metric in METRICS:
        for basis, currency in _VIEWS:
            annual = sorted(
                (
                    choice
                    for (name, period, view_basis, view_currency), choice in chosen.items()
                    if name == metric
                    and period.startswith("FY")
                    and (view_basis, view_currency) == (basis, currency)
                ),
                key=lambda choice: choice.fact.period,
                reverse=True,
            )
            if annual:
                shown.extend(_key_fact(choice, citations) for choice in annual[:years])
                break
    return shown


def _view(
    name: str, label: str, derived: Derived, citations: dict[int, Citation], rows: list[FactRow]
) -> DerivedView:
    by_id = {row.id: row for row in rows}
    return DerivedView(
        name=name,
        label=label,
        status=derived.status,
        value=derived.value,
        reason=derived.reason,
        citations=tuple(citations[fact_id] for fact_id in derived.fact_ids),
        inputs=tuple(by_id[fact_id] for fact_id in derived.fact_ids),
    )


def derived_views(facts: list[StoredFact], *, is_financial: bool) -> list[DerivedView]:
    """The four derived values, always all four and in this order."""
    rows = [stored.row for stored in facts]
    citations = {stored.row.id: stored.citation for stored in facts}
    top_line = "net_interest_income" if is_financial else "revenue_from_operations"
    top_label = "Net interest income growth" if is_financial else "Revenue growth"
    return [
        _view(
            "debt_to_equity",
            "Debt to equity",
            debt_to_equity(rows, is_financial=is_financial),
            citations,
            rows,
        ),
        _view("revenue_growth", top_label, growth_yoy(rows, top_line), citations, rows),
        _view(
            "profit_growth", "Net profit growth", growth_yoy(rows, "net_profit"), citations, rows
        ),
        _view("latest_dividend", "Latest dividend", latest_dividend(rows), citations, rows),
    ]


# --- one measure for one answer -------------------------------------------------------------------


def _latest_years(rows: list[FactRow], years: int) -> list[str]:
    full_years = sorted({row.period for row in rows if row.period.startswith("FY")}, reverse=True)
    return full_years[:years]


def _measured(
    fact: FactRow,
    view_rows: list[FactRow],
    citations: dict[int, Citation],
    labels: dict[int, str | None],
) -> KeyFact:
    """The figure shown, with every other source's best figure for its period: agreeing ones
    counted, differing ones kept as rivals."""
    agreeing = 0
    rivals: list[Rival] = []
    for source in SOURCE_RANK:
        others = [r for r in view_rows if r.period == fact.period and r.source == source]
        if source == fact.source or not others:
            continue
        best = choose_all(others)[(fact.metric, fact.period, fact.basis, fact.currency)].fact
        if agrees(best.value, fact.value):
            agreeing += 1
        else:
            rival = Rival(citations[best.id], best.value, best.unit, labels.get(best.id))
            rivals.append(rival)
    status = "disputed" if rivals else ("agreed" if agreeing else "single")
    return KeyFact(
        metric=fact.metric,
        label=METRIC_LABELS[fact.metric],
        period=fact.period,
        basis=fact.basis,
        currency=fact.currency,
        unit=fact.unit,
        value=fact.value,
        status=status,
        corroborated_by=agreeing,
        citation=citations[fact.id],
        disputed_by=tuple(rival.citation for rival in rivals),
        rivals=tuple(rivals),
        row=fact,
        reported_as=labels.get(fact.id),
    )


def _one_source(view_rows: list[FactRow], wanted: list[str]) -> list[FactRow]:
    """The wanted periods from the best-ranked source that has them all; failing that, each
    period's own best figure (the sources then differ, and no change is computed across them)."""
    for source in SOURCE_RANK:
        chosen = {
            key[1]: winner.fact
            for key, winner in choose_all([r for r in view_rows if r.source == source]).items()
        }
        if all(period in chosen for period in wanted):
            return [chosen[period] for period in wanted]
    best = {key[1]: winner.fact for key, winner in choose_all(view_rows).items()}
    return [best[period] for period in wanted if period in best]


def measure_facts(
    facts: list[StoredFact],
    metric: str,
    *,
    periods: tuple[str, ...] = (),
    years: int = YEARS_SHOWN,
    basis: str | None = None,
) -> list[KeyFact]:
    """One metric's figures for an answer, newest first, from one source where possible: the
    named periods, else the latest ``years`` full years, in the first view (consolidated ₹ first,
    or the basis asked for) that has any of them."""
    citations = {stored.row.id: stored.citation for stored in facts}
    labels = {stored.row.id: stored.reported_as for stored in facts}
    rows = [stored.row for stored in facts if stored.row.metric == metric]
    views = sorted(_VIEWS, key=lambda view: view[0] != basis) if basis else _VIEWS
    for view_basis, view_currency in views:
        view_rows = [r for r in rows if (r.basis, r.currency) == (view_basis, view_currency)]
        wanted = list(periods) if periods else _latest_years(view_rows, years)
        picked = sorted(_one_source(view_rows, wanted), key=lambda r: r.period_end, reverse=True)
        if picked:
            return [_measured(fact, view_rows, citations, labels) for fact in picked]
    return []


def _change(earlier: KeyFact, later: KeyFact) -> DerivedView | None:
    first, second = earlier.row, later.row
    if first is None or second is None:
        return None
    same_kind = first.period[:2] == second.period[:2]  # two years, or the same quarter twice
    like_with_like = (first.source, first.basis, first.currency, first.unit) == (
        second.source,
        second.basis,
        second.currency,
        second.unit,
    )
    if not (same_kind and like_with_like):
        return None
    words = METRIC_LABELS[first.metric].lower()
    label = f"{METRIC_LABELS[first.metric]} change, {first.period} to {second.period}"
    citations = (earlier.citation, later.citation)
    if first.value <= 0:
        reason = (
            f"Not assessable: {words} for {first.period} is zero or negative, so a percent "
            "change has no meaning."
        )
        return DerivedView("change", label, "not_assessable", None, reason, citations, (first,))
    change = (second.value - first.value) / first.value * 100
    value = change.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    reason = (
        f"Change in {words} from {first.period} to {second.period}, {second.basis} figures "
        f"from {SOURCE_WORDS[second.source]}."
    )
    difference = second.value - first.value
    inputs = (first, second)
    return DerivedView("change", label, "ok", value, reason, citations, inputs, difference)


def change_views(picked: list[KeyFact]) -> list[DerivedView]:
    """The change between each two consecutive periods of one measure (newest first, as
    measure_facts returns them), when both come from one source on one basis."""
    views = [_change(earlier, later) for later, earlier in pairwise(picked)]
    return [view for view in views if view is not None]


def recent_events(events: list[StoredEvent], *, limit: int = EVENTS_SHOWN) -> list[StoredEvent]:
    """Newest first (then the newest row), at most ``limit``."""
    ordered = sorted(events, key=lambda e: (e.row.event_date, e.row.id), reverse=True)
    return ordered[:limit]
