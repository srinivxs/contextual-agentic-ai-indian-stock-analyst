"""The year-by-year table under an answer about one stock's profit or revenue (redesign).

Built by code from screener.in's stored series (app/series.py), never by the model, so it needs no
checking: every figure is a stored row, shown in rupees crore as reported, and the change is plain
arithmetic on two of those rows. One stock, one measure, one source: like with like.

fits_answer (the owner's review, 2026-09-29): the table is shown only beside an answer about its
measure (one that cites a figure of it) whose every figure of that measure is consolidated and,
for each year the table shows, the very figure the table shows. An answer resting on an annual
report's figure that screener.in counts differently gets no table: never one figure in the text
and another in the table.
"""

from decimal import ROUND_HALF_UP, Decimal

from app.chat.contract import DataTable
from app.chat.evidence import EvidenceItem, _indian, _plain  # the answer's Indian grouping
from app.chat.understand import Question
from app.insights import METRIC_LABELS
from app.series import SeriesPoint

# The measures a table can show, by what the question asked for. A bank's "revenue" is its net
# interest income, so understand() names both and the one that has figures is used.
PROFIT = ("net_profit",)
REVENUE = ("revenue_from_operations", "net_interest_income")
SERIES_METRICS = PROFIT + REVENUE

MAX_ROWS = 5
SOURCE_LABEL = "screener.in · consolidated, full years"
_MINUS = chr(0x2212)  # a real minus sign, not a hyphen
_TENTH = Decimal("0.1")


def _year(point: SeriesPoint) -> int:
    return int(point.period[2:])  # "FY2026" -> 2026 (load_series only returns this shape)


def _shown(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    return sign + _indian(_plain(abs(value)))


def _change(point: SeriesPoint, before: SeriesPoint | None) -> str:
    """ "+1.3%" / "minus 2.1%" on the year before; "" when that year is not stored or was zero."""
    if before is None or _year(before) != _year(point) - 1 or before.value == 0:
        return ""
    percent = ((point.value - before.value) / abs(before.value) * 100).quantize(
        _TENTH, rounding=ROUND_HALF_UP
    )
    if percent > 0:
        return f"+{percent}%"
    if percent < 0:
        return f"{_MINUS}{abs(percent)}%"
    return "0.0%"


def _measure(question: Question) -> tuple[str, ...]:
    """The candidate metrics the question asks about; () when it asks for none, or for both a
    profit and a revenue (one table cannot answer both)."""
    asks_profit = any(m in PROFIT for m in question.metrics)
    asks_revenue = any(m in REVENUE for m in question.metrics)
    if asks_profit == asks_revenue:
        return ()
    return PROFIT if asks_profit else tuple(m for m in REVENUE if m in question.metrics)


def _chosen(
    question: Question, series: dict[str, list[SeriesPoint]]
) -> tuple[str, list[SeriesPoint]] | None:
    """The metric and points the table shows: one named stock and at least two years."""
    if len(question.symbols) != 1:
        return None
    for metric in _measure(question):
        points = series.get(metric, [])
        if len(points) >= 2:
            return metric, points
    return None


def fits_answer(
    question: Question, series: dict[str, list[SeriesPoint]], cited: list[EvidenceItem]
) -> bool:
    """Whether the table may be shown beside an answer citing these items."""
    chosen = _chosen(question, series)
    if chosen is None:
        return False
    metric, points = chosen
    shown = {point.period: point.value for point in points[-MAX_ROWS:]}
    mine = [
        figure
        for item in cited
        for figure in item.figures
        if figure.symbol == question.symbols[0] and figure.metric == metric
    ]
    return bool(mine) and all(
        figure.basis == "consolidated" and shown.get(figure.period, figure.value) == figure.value
        for figure in mine
    )


def build_table(question: Question, series: dict[str, list[SeriesPoint]]) -> DataTable | None:
    """The table for the question, or None. ``series``: each candidate metric's points, oldest
    first (app/series.py). A table needs exactly one named stock and at least two years."""
    chosen = _chosen(question, series)
    if chosen is None:
        return None
    metric, points = chosen
    label = METRIC_LABELS[metric]
    newest_first = list(reversed(points))
    rows = tuple(
        (
            point.period,
            _shown(point.value),
            _change(point, newest_first[i + 1] if i + 1 < len(newest_first) else None),
        )
        for i, point in enumerate(newest_first[:MAX_ROWS])
    )
    return DataTable(
        title=f"{question.symbols[0]} {label.lower()} (consolidated, ₹ crore)",
        columns=("Year", f"{label} (₹ crore)", "Change"),
        rows=rows,
        source_label=SOURCE_LABEL,
        source_url=points[-1].source_url,
    )
