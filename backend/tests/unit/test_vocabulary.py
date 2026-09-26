"""The fixed vocabulary the fact extractor may use (P11): metrics, units and event labels.

The model may only propose facts for these ten metrics; anything else is rejected before it is
looked at. The synonyms are what the validator looks for in the quote, so they are tested here
against realistic (fictional DemoCo) phrasing.
"""

import re

import pytest

from app.vocabulary import (
    BASES,
    EVENT_TYPES,
    IMPACTS,
    METRICS,
    SENTIMENTS,
    Metric,
    metrics_for,
    unit_for,
)


def test_there_are_exactly_the_ten_agreed_metrics() -> None:
    assert set(METRICS) == {
        "revenue_from_operations",
        "net_interest_income",
        "net_profit",
        "total_borrowings",
        "total_equity",
        "dividend_per_share",
        "eps_basic",
        "return_on_equity",
        "net_interest_margin",
        "gross_npa_ratio",
    }


def test_each_metric_is_filed_under_its_own_name_and_has_a_description() -> None:
    for name, metric in METRICS.items():
        assert metric.name == name
        assert metric.description
        assert "\n" not in metric.description  # one line: it goes into the prompt
        assert metric.synonyms


@pytest.mark.parametrize(
    ("name", "kind", "applies_to"),
    [
        ("revenue_from_operations", "amount", "non_financial"),
        ("net_interest_income", "amount", "bank"),
        ("net_profit", "amount", "all"),
        ("total_borrowings", "amount", "non_financial"),
        ("total_equity", "amount", "all"),
        ("dividend_per_share", "per_share", "all"),
        ("eps_basic", "per_share", "all"),
        ("return_on_equity", "percent", "all"),
        ("net_interest_margin", "percent", "bank"),
        ("gross_npa_ratio", "percent", "bank"),
    ],
)
def test_kind_and_audience_of_each_metric(name: str, kind: str, applies_to: str) -> None:
    assert (METRICS[name].kind, METRICS[name].applies_to) == (kind, applies_to)


def test_metrics_are_immutable() -> None:
    with pytest.raises(AttributeError):
        METRICS["net_profit"].kind = "percent"  # type: ignore[misc]


def test_a_bank_gets_the_common_metrics_and_the_bank_ones() -> None:
    names = [m.name for m in metrics_for(is_financial=True)]
    assert names == [
        "net_interest_income",
        "net_profit",
        "total_equity",
        "dividend_per_share",
        "eps_basic",
        "return_on_equity",
        "net_interest_margin",
        "gross_npa_ratio",
    ]


def test_a_non_financial_company_gets_the_common_metrics_and_revenue_and_borrowings() -> None:
    names = [m.name for m in metrics_for(is_financial=False)]
    assert names == [
        "revenue_from_operations",
        "net_profit",
        "total_borrowings",
        "total_equity",
        "dividend_per_share",
        "eps_basic",
        "return_on_equity",
    ]


@pytest.mark.parametrize(
    ("kind", "currency", "unit"),
    [
        ("amount", "INR", "INR_CRORE"),
        ("amount", "USD", "USD_MILLION"),
        ("per_share", "INR", "INR_PER_SHARE"),
        ("per_share", "USD", "USD_PER_SHARE"),
        ("percent", None, "PERCENT"),
        ("percent", "INR", "PERCENT"),
        ("percent", "USD", "PERCENT"),
    ],
)
def test_unit_for(kind: str, currency: str | None, unit: str) -> None:
    assert unit_for(kind, currency) == unit  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", ["amount", "per_share"])
def test_a_money_figure_without_a_currency_has_no_unit(kind: str) -> None:
    with pytest.raises(ValueError, match="currency"):
        unit_for(kind, None)  # type: ignore[arg-type]


def test_event_and_label_vocabularies() -> None:
    assert EVENT_TYPES == (
        "earnings_results",
        "guidance_outlook",
        "dividend",
        "credit_rating",
        "debt_or_capital_raise",
        "merger_acquisition",
        "management_change",
        "regulatory_legal",
        "order_win_partnership",
        "investor_meeting",
        "other",
    )
    assert SENTIMENTS == ("negative", "neutral", "positive")
    assert IMPACTS == ("low", "medium", "high")
    assert BASES == ("consolidated", "standalone", "unspecified")


def _mentions(metric: Metric, text: str) -> bool:
    return any(re.search(rf"\b(?:{s})\b", text) for s in metric.synonyms)


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("revenue_from_operations", "revenue from operations 26 4,12,345"),
        ("revenue_from_operations", "total income from operations grew"),
        ("net_interest_income", "net interest income rose"),
        ("net_interest_income", "nii grew 12%"),
        ("net_profit", "net profit stood at 1,234 crore"),
        ("net_profit", "profit after tax 1,234"),
        ("net_profit", "pat of 1,234 crore"),
        ("net_profit", "profit for the year 1,234"),
        ("net_profit", "profit for the quarter 321"),
        ("total_borrowings", "total borrowings 9,876"),
        ("total_borrowings", "borrowings 9,876"),
        ("total_borrowings", "total debt of 9,876 crore"),
        ("total_borrowings", "gross debt 9,876"),
        ("total_equity", "total equity 45,678"),
        ("total_equity", "net worth 45,678"),
        ("total_equity", "shareholders' funds 45,678"),
        ("total_equity", "shareholders funds 45,678"),
        ("total_equity", "equity attributable to owners 45,678"),
        ("dividend_per_share", "dividend per equity share 10"),
        ("dividend_per_share", "dividend per share 10"),
        ("dividend_per_share", "a final dividend of 10 per equity share"),
        ("dividend_per_share", "interim dividend of 5 per share"),
        ("dividend_per_share", "dps 10"),
        ("eps_basic", "earnings per equity share basic 45.20"),
        ("eps_basic", "basic eps 45.20"),
        ("return_on_equity", "return on equity 18.5%"),
        ("return_on_equity", "return on average equity 18.5%"),
        ("return_on_equity", "roe of 18.5%"),
        ("return_on_equity", "ronw 18.5%"),
        ("net_interest_margin", "net interest margin 4.1%"),
        ("net_interest_margin", "nim at 4.1%"),
        ("gross_npa_ratio", "gross npa 1.2%"),
        ("gross_npa_ratio", "gross npas were 1.2%"),
        ("gross_npa_ratio", "gross non-performing assets 1.2%"),
        ("gross_npa_ratio", "gross non performing assets 1.2%"),
    ],
)
def test_synonyms_recognise_the_usual_labels(name: str, text: str) -> None:
    assert _mentions(METRICS[name], text)


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("net_profit", "democo filed a patent"),  # "pat" only as a whole word
        ("total_borrowings", "net debt of 1,234 crore"),  # net debt subtracts cash
        ("net_interest_income", "net income of 1,234"),
        ("revenue_from_operations", "net profit 1,234"),
        ("eps_basic", "dividend per share 10"),
    ],
)
def test_synonyms_do_not_fire_on_look_alikes(name: str, text: str) -> None:
    assert not _mentions(METRICS[name], text)


def test_revenue_means_the_statutory_line_item_not_any_revenue() -> None:
    """Found in the first real run: Reliance's headline "Consolidated revenue ... ₹X crore" is its
    Value of Sales and Services (gross of taxes), not Revenue from Operations; accepting the loose
    word mixed two definitions and produced a 20% "growth" that was really a definition change."""
    import re

    synonyms = METRICS["revenue_from_operations"].synonyms
    label = re.compile(r"\b(?:" + "|".join(synonyms) + r")\b")
    assert label.search("revenue from operations 26 4,12,345")
    assert label.search("total income from operations")
    for loose in ("consolidated revenue grew", "net sales of", "sales", "turnover"):
        assert not label.search(loose), loose
