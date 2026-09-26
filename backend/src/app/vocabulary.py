"""The fixed vocabulary of the fact extractor (P11): ten metrics, their units, and event labels.

The model reading a filing may only propose facts for the metrics listed here. A small, closed list
is what makes the answers checkable: each metric says what kind of number it is (an amount, a
per-share figure or a percentage), which companies it applies to (banks report net interest
income, not revenue from operations), and which words a quote must contain to count as being about
it.

Synonyms are regular-expression fragments matched against the validator's normalised text
(app/fact_validation.py: lowercase, one space between words, rupee signs removed). The validator
wraps each one in word boundaries, so "pat" matches "PAT of" but not "patent".

Canonical units: rupee amounts are stored in crore and US-dollar amounts in million, as reported
(the owner's decision: dollars are never converted to rupees).
"""

from dataclasses import dataclass
from typing import Literal

Unit = Literal["INR_CRORE", "USD_MILLION", "INR_PER_SHARE", "USD_PER_SHARE", "PERCENT"]
MetricKind = Literal["amount", "per_share", "percent"]
AppliesTo = Literal["all", "non_financial", "bank"]
Currency = Literal["INR", "USD"]


@dataclass(frozen=True)
class Metric:
    name: str
    kind: MetricKind
    applies_to: AppliesTo
    synonyms: tuple[str, ...]  # regex fragments, lowercase, matched as whole words
    description: str  # one line; goes into the extraction prompt


_ALL_METRICS = (
    Metric(
        name="revenue_from_operations",
        kind="amount",
        applies_to="non_financial",
        # The statutory line item only. Found in the first real run: a company's headline
        # "revenue" can be a different figure (Reliance's Value of Sales and Services is gross of
        # taxes), and accepting the loose word mixed two definitions into one series.
        synonyms=(
            "revenue from operations",
            "total income from operations",
            "income from operations",
        ),
        description=(
            "Revenue from operations, the statutory line item of the profit and loss statement "
            "(not a headline 'revenue' or 'value of sales' that may include taxes)."
        ),
    ),
    Metric(
        name="net_interest_income",
        kind="amount",
        applies_to="bank",
        synonyms=("net interest income", "nii"),
        description="A bank's net interest income: interest earned minus interest paid.",
    ),
    Metric(
        name="net_profit",
        kind="amount",
        applies_to="all",
        synonyms=(
            "net profit",
            "profit after tax",
            "pat",
            "profit for the (?:year|period|quarter)",
        ),
        description="Net profit (profit after tax) for the period; a loss is negative.",
    ),
    Metric(
        name="total_borrowings",
        kind="amount",
        applies_to="non_financial",
        # Plain "debt", but not "net debt": net debt subtracts cash and is a different number.
        synonyms=("total borrowings", "borrowings", "total debt", "gross debt", "(?<!net )debt"),
        description="Total borrowings (gross debt) at the end of the period.",
    ),
    Metric(
        name="total_equity",
        kind="amount",
        applies_to="all",
        synonyms=(
            "total equity",
            "net worth",
            "shareholders'? funds",
            "shareholders'? equity",
            "equity attributable",
        ),
        description="Total equity (net worth, shareholders' funds) at the end of the period.",
    ),
    Metric(
        name="dividend_per_share",
        kind="per_share",
        applies_to="all",
        synonyms=(
            "dividend per (?:equity )?share",
            "dividend of",
            "final dividend",
            "interim dividend",
            "special dividend",
            "dps",
        ),
        description="Dividend declared or recommended per equity share.",
    ),
    Metric(
        name="eps_basic",
        kind="per_share",
        applies_to="all",
        synonyms=("earnings per (?:equity )?share", "eps"),
        description="Basic earnings per share for the period.",
    ),
    Metric(
        name="return_on_equity",
        kind="percent",
        applies_to="all",
        synonyms=("return on (?:average )?(?:equity|net worth)", "roe", "roae", "ronw"),
        description="Return on equity (return on net worth), as a percentage.",
    ),
    Metric(
        name="net_interest_margin",
        kind="percent",
        applies_to="bank",
        synonyms=("net interest margin", "nim"),
        description="A bank's net interest margin, as a percentage.",
    ),
    Metric(
        name="gross_npa_ratio",
        kind="percent",
        applies_to="bank",
        synonyms=("gross npas?", "gross non[- ]performing", "gnpa"),
        description="A bank's gross non-performing assets as a percentage of its advances.",
    ),
)

METRICS: dict[str, Metric] = {metric.name: metric for metric in _ALL_METRICS}

EVENT_TYPES = (
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
SENTIMENTS = ("negative", "neutral", "positive")
IMPACTS = ("low", "medium", "high")
BASES = ("consolidated", "standalone", "unspecified")


def metrics_for(is_financial: bool) -> list[Metric]:
    """The metrics that make sense for one company: the common ones plus its own kind's."""
    own_kind = "bank" if is_financial else "non_financial"
    return [m for m in METRICS.values() if m.applies_to in ("all", own_kind)]


def unit_for(kind: MetricKind, currency: Currency | None) -> Unit:
    """The canonical unit a value of this kind is stored in."""
    if kind == "percent":
        return "PERCENT"
    if currency is None:
        raise ValueError(f"a money figure ({kind}) needs a currency")
    if kind == "amount":
        return "INR_CRORE" if currency == "INR" else "USD_MILLION"
    return "INR_PER_SHARE" if currency == "INR" else "USD_PER_SHARE"
