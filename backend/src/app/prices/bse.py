"""BSE's daily price file: fetch one day politely, pick our stocks' rows out of it (ADR 025).

The file ("bhavcopy") is one CSV per trading day listing every security; we keep only the rows
whose FinInstrmId is the BSE code of a stock we follow. Nothing is stored here: the caller does.

BSE answers requests a few seconds apart with HTTP 406 and a small HTML page, and the same file
downloads fine after a pause. Days without trading (weekends, market holidays) have no file and
answer 404 or 406 as well. From outside, "too fast" and "no such file" look the same, so both are
reported as "slow_down" and the JOB decides what to do (app/price_jobs.py). A 200 whose body is
not the CSV (an error page with a friendly status) is treated the same way. Anything else (500,
403, a network error) raises, and the job retries with backoff.
"""

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

import httpx

from app.polite_fetch import polite_fetch
from app.prices.model import DailyPrice, FetchOutcome, bhavcopy_url

FILE_LIMIT = 5 * 1024 * 1024  # the real file is about 0.9 MB

REQUIRED_COLUMNS = (
    "TradDt",
    "FinInstrmTp",
    "FinInstrmId",
    "OpnPric",
    "HghPric",
    "LwPric",
    "ClsPric",
    "PrvsClsgPric",
    "TtlTradgVol",
)


class BadBhavcopy(ValueError):
    """The body is not BSE's daily price file (an HTML error page, an empty answer)."""


def is_bhavcopy_url(url: str) -> bool:
    """https on www.bseindia.com, no port and no userinfo. Nothing else is ever requested."""
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.netloc == "www.bseindia.com"
        and parts.hostname == "www.bseindia.com"
    )


def _price(text: str) -> Decimal | None:
    """A positive, finite price, or None."""
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None


def _volume(text: str) -> int | None:
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        return None
    return int(value) if value.is_finite() and value >= 0 else None


def _row_to_price(record: dict[str, str]) -> DailyPrice | None:
    try:
        day = date.fromisoformat(record["TradDt"].strip())
    except ValueError:
        return None
    open_, high, low = (
        _price(record["OpnPric"]),
        _price(record["HghPric"]),
        _price(record["LwPric"]),
    )
    close, prev = _price(record["ClsPric"]), _price(record["PrvsClsgPric"])
    volume = _volume(record["TtlTradgVol"])
    if open_ is None or high is None or low is None or close is None or prev is None:
        return None
    if volume is None:
        return None
    return DailyPrice(
        bse_code=record["FinInstrmId"].strip(),
        trade_date=day,
        open=open_,
        high=high,
        low=low,
        close=close,
        prev_close=prev,
        volume=volume,
    )


def parse_bhavcopy(body: bytes, codes: set[str]) -> list[DailyPrice]:
    """The rows of ``body`` that are equities (STK) with a BSE code in ``codes``.

    A row with a bad number or date is skipped, not fatal. A body whose header is not the file's
    raises ``BadBhavcopy``: that is an error page, and storing nothing is the right answer.
    """
    text = body.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    header = reader.fieldnames or []
    if not all(column in header for column in REQUIRED_COLUMNS):
        raise BadBhavcopy("the answer is not BSE's daily price file")
    prices: list[DailyPrice] = []
    for record in reader:
        if None in record.values():  # a short row: csv fills the missing cells with None
            continue
        if record["FinInstrmTp"].strip() != "STK" or record["FinInstrmId"].strip() not in codes:
            continue
        price = _row_to_price(record)
        if price is not None:
            prices.append(price)
    return prices


async def fetch_day(
    http: httpx.AsyncClient, day: date, *, codes: set[str]
) -> tuple[FetchOutcome, list[DailyPrice]]:
    """One day's file. ("fetched", rows) or ("slow_down", []); other failures raise."""
    try:
        fetched = await polite_fetch(
            http, bhavcopy_url(day), allowed=is_bhavcopy_url, limit=FILE_LIMIT
        )
    except httpx.HTTPStatusError as error:
        if error.response.status_code in (404, 406):
            return "slow_down", []
        raise
    try:
        return "fetched", parse_bhavcopy(fetched.body, codes)
    except BadBhavcopy:
        return "slow_down", []
