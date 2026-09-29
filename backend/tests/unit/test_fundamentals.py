"""The Fundamentals card (the owner, 2026-09-30): screener.in's top ratios as stored, two values
computed from them, our own debt to equity, and what the page does not give said plainly."""

from datetime import UTC, datetime
from decimal import Decimal

from app.fundamentals import Ratios, fundamentals
from app.insights import DerivedView

URL = "https://www.screener.in/company/DEMO/consolidated/"


def ratios(**overrides: Decimal | None) -> Ratios:
    values: dict[str, Decimal | None] = {
        "market_cap_crore": Decimal("1234567"),
        "current_price": Decimal("2000"),
        "stock_pe": Decimal("25"),
        "book_value": Decimal("400"),
        "dividend_yield_percent": Decimal("1.5"),
        "roce_percent": Decimal("20"),
        "roe_percent": Decimal("18.2"),
        "face_value": Decimal("1"),
    }
    values.update(overrides)
    return Ratios(**values, source_url=URL, fetched_at=datetime(2026, 9, 30, tzinfo=UTC))


def debt(
    status: str = "ok", value: str | None = "0.37", reason: str = "Borrowings over equity."
) -> DerivedView:
    return DerivedView(
        name="debt_to_equity",
        label="Debt to equity",
        status=status,
        value=Decimal(value) if value is not None else None,
        reason=reason,
        citations=(),
    )


def shown(items: list) -> list[tuple[str, str, str | None, str]]:  # type: ignore[type-arg]
    return [(i.label, i.status, None if i.value is None else str(i.value), i.source) for i in items]


def test_the_ten_fundamentals_in_the_owner_s_order() -> None:
    assert shown(fundamentals(ratios(), debt())) == [
        ("Mkt Cap", "ok", "1234567", "screener"),
        ("ROE", "ok", "18.2", "screener"),
        ("P/E Ratio (TTM)", "ok", "25", "screener"),
        ("EPS (TTM)", "ok", "80.00", "computed"),  # price 2000 / P/E 25
        ("P/B Ratio", "ok", "5.00", "computed"),  # price 2000 / book value 400
        ("Div Yield", "ok", "1.5", "screener"),
        ("Industry P/E", "not_available", None, "none"),
        ("Book Value", "ok", "400", "screener"),
        ("Debt to Equity", "ok", "0.37", "computed"),
        ("Face Value", "ok", "1", "screener"),
    ]


def test_each_value_carries_its_unit_and_computed_ones_say_how() -> None:
    items = {item.label: item for item in fundamentals(ratios(), debt())}
    assert [items[k].unit for k in ("Mkt Cap", "ROE", "EPS (TTM)", "P/B Ratio", "Face Value")] == [
        "INR_CRORE",
        "PERCENT",
        "INR_PER_SHARE",
        "RATIO",
        "INR_PER_SHARE",
    ]
    assert items["EPS (TTM)"].note == "Computed: current price ₹2000 divided by P/E 25."
    assert items["P/B Ratio"].note == "Computed: current price ₹2000 divided by book value ₹400."
    assert items["Industry P/E"].note == (
        "Not in the data: the screener.in company page this app reads does not show it."
    )
    assert items["Debt to Equity"].note == "Borrowings over equity."


def test_what_the_page_lacks_is_not_available_never_zero() -> None:
    items = {i.label: i for i in fundamentals(ratios(book_value=None, stock_pe=None), debt())}
    assert (items["Book Value"].status, items["Book Value"].value) == ("not_available", None)
    assert items["P/E Ratio (TTM)"].status == "not_available"
    assert items["EPS (TTM)"].status == "not_available"  # it needs the P/E
    assert items["P/B Ratio"].status == "not_available"  # it needs the book value


def test_a_zero_or_negative_divisor_gives_no_computed_value() -> None:
    items = {i.label: i for i in fundamentals(ratios(stock_pe=Decimal(0)), debt())}
    assert items["EPS (TTM)"].status == "not_available"


def test_a_bank_has_no_debt_to_equity_and_says_why() -> None:
    reason = "Debt to equity does not apply to banks: borrowing is their business."
    item = fundamentals(ratios(), debt("not_applicable", None, reason))[8]
    assert (item.label, item.status, item.value, item.note) == (
        "Debt to Equity",
        "not_applicable",
        None,
        reason,
    )


def test_with_no_page_read_yet_only_our_own_value_is_there() -> None:
    items = fundamentals(None, debt())
    assert [i.status for i in items].count("ok") == 1
    assert items[8].label == "Debt to Equity"
