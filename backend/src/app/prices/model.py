"""The shapes of share prices (ADR 025). Every part of the prices phase agrees on this module.

    BSE's daily price file ("bhavcopy", one CSV per trading day, every listed security)
        ──> app/prices/bse.py: the file's address for a date, a polite fetch, the three rows
            we follow picked out by BSE code (stocks.bse_code) -> DailyPrice
        ──> app/price_jobs.py: the sync_prices job (a few dates per run, newest first, a long
            pause between files, stop at the first "slow down"), prices stored once per stock
            and day (unique (stock_id, trade_date), ON CONFLICT DO NOTHING)
        ──> app/prices/derived.py: computed on read (ADR 009): the history adjusted for bonus
            issues and splits, returns, volatility, P/E and dividend yield

A bonus issue or split. On the day a stock goes ex-bonus, BSE's file gives the previous close
ALREADY ADJUSTED (PrvsClsgPric), while our stored close for the day before is the raw figure.
So for two consecutive stored trading days, factor = prev_close(today) / close(yesterday): 1.0 on
an ordinary day, 0.5 after a 1:1 bonus. Earlier closes are multiplied by the product of the later
factors (back-adjusted), so a bonus never looks like a crash. Factors within 1% of 1.0 are
treated as 1.0 (rounding in the file).

No live prices, no indices (the equity file has none), nothing but the three stocks' rows kept.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

BHAVCOPY_URL = (
    "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{yyyymmdd}_F_0000.CSV"
)
# How a price is cited: "BSE daily price file · 28 Sep 2026", linking to that day's file.
PRICE_SOURCE_LABEL = "BSE daily price file"
ACTION_TOLERANCE = Decimal("0.01")  # a factor within 1% of 1 is rounding, not a corporate action


def bhavcopy_url(day: date) -> str:
    return BHAVCOPY_URL.format(yyyymmdd=day.strftime("%Y%m%d"))


@dataclass(frozen=True)
class DailyPrice:
    """One stock's row of one day's file, as stored (table prices)."""

    bse_code: str  # FinInstrmId in the file; stocks.bse_code
    trade_date: date  # TradDt
    open: Decimal  # OpnPric
    high: Decimal  # HghPric
    low: Decimal  # LwPric
    close: Decimal  # ClsPric
    prev_close: Decimal  # PrvsClsgPric: the previous close, adjusted by BSE for any action today
    volume: int  # TtlTradgVol


FetchOutcome = Literal["fetched", "slow_down"]  # slow_down: BSE answered 406/404 or not a CSV


@dataclass(frozen=True)
class AdjustedClose:
    trade_date: date
    close: Decimal  # adjusted for later bonus issues and splits
    raw_close: Decimal  # as in the file


@dataclass(frozen=True)
class CorporateAction:
    trade_date: date  # the ex-date
    factor: Decimal  # prev_close(ex-date) / close(day before): 0.5 for a 1:1 bonus
