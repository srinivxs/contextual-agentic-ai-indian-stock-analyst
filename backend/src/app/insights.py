"""What the stock page shows, computed on read from stored facts and events (P11c, ADR 020).

Everything here is a pure function over rows the API has already loaded, so it is tested without a
database. The choices are app/derived.py's (the owner's conflict policy, ADR 009); this module only
decides what to show and attaches a citation to every figure:

- key facts: per metric one view (consolidated ₹ first, then standalone, then basis not stated; a
  US$ figure only when no ₹ one exists, shown as reported), the latest three fiscal years;
- derived values: debt to equity, growth of revenue (net interest income for a bank) and of net
  profit, the latest dividend, each citing the facts it used;
- recent events, newest first.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from app.derived import (
    Chosen,
    Derived,
    EventRow,
    FactRow,
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
    source: Literal["filing", "screener"]
    label: str
    url: str | None
    quote: str | None


@dataclass(frozen=True)
class StoredFact:
    row: FactRow
    citation: Citation


@dataclass(frozen=True)
class StoredEvent:
    row: EventRow
    summary: str
    citation: Citation


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


@dataclass(frozen=True)
class DerivedView:
    name: str
    label: str
    status: str
    value: Decimal | None
    reason: str
    citations: tuple[Citation, ...]


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


def _view(name: str, label: str, derived: Derived, citations: dict[int, Citation]) -> DerivedView:
    return DerivedView(
        name=name,
        label=label,
        status=derived.status,
        value=derived.value,
        reason=derived.reason,
        citations=tuple(citations[fact_id] for fact_id in derived.fact_ids),
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
        ),
        _view("revenue_growth", top_label, growth_yoy(rows, top_line), citations),
        _view("profit_growth", "Net profit growth", growth_yoy(rows, "net_profit"), citations),
        _view("latest_dividend", "Latest dividend", latest_dividend(rows), citations),
    ]


def recent_events(events: list[StoredEvent], *, limit: int = EVENTS_SHOWN) -> list[StoredEvent]:
    """Newest first (then the newest row), at most ``limit``."""
    ordered = sorted(events, key=lambda e: (e.row.event_date, e.row.id), reverse=True)
    return ordered[:limit]
