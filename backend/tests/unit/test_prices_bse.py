"""BSE's daily price file: reading it, and fetching it politely (ADR 025). Synthetic files only:
DemoCo and its friends have BSE codes 9999xx, which are not real."""

from datetime import date
from decimal import Decimal

import httpx
import pytest

from app.polite_fetch import USER_AGENT, FetchRefused
from app.prices.bse import BadBhavcopy, fetch_day, is_bhavcopy_url, parse_bhavcopy
from app.prices.model import DailyPrice, bhavcopy_url

HEADER = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
    "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,"
    "PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,"
    "TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4"
)
DEMO = (
    "2026-09-28,2026-09-28,CM,BSE,STK,999901,INE000X01011,DEMOCO,A,,,,,DEMOCO LTD.,731.25,"
    "733.00,718.05,718.85,718.85,735.80,,719.05,,,1425407,1028753302.00,39915,F1,1,,,,,"
)
DAY = date(2026, 9, 28)


def row(code: str = "999901", kind: str = "STK", **fields: str) -> str:
    cells = DEMO.split(",")
    cells[5] = code
    cells[4] = kind
    names = HEADER.split(",")
    for name, value in fields.items():
        cells[names.index(name)] = value
    return ",".join(cells)


def csv(*rows: str, header: str = HEADER) -> bytes:
    return ("\r\n".join([header, *rows]) + "\r\n").encode()


# --- parsing ----------------------------------------------------------------------------------


def test_a_row_of_the_real_header_shape_becomes_a_daily_price() -> None:
    assert parse_bhavcopy(csv(DEMO), {"999901"}) == [
        DailyPrice(
            bse_code="999901",
            trade_date=DAY,
            open=Decimal("731.25"),
            high=Decimal("733.00"),
            low=Decimal("718.05"),
            close=Decimal("718.85"),
            prev_close=Decimal("735.80"),
            volume=1425407,
        )
    ]


def test_only_the_codes_we_follow_are_kept() -> None:
    body = csv(row("999901"), row("999902"), row("999903"))
    assert [p.bse_code for p in parse_bhavcopy(body, {"999901", "999903"})] == [
        "999901",
        "999903",
    ]


def test_no_codes_means_no_rows() -> None:
    assert parse_bhavcopy(csv(DEMO), set()) == []


def test_only_equity_rows_are_kept() -> None:
    body = csv(row(kind="IDX"), row(kind="STK"))
    assert len(parse_bhavcopy(body, {"999901"})) == 1


def test_a_byte_order_mark_is_tolerated() -> None:
    assert len(parse_bhavcopy(b"\xef\xbb\xbf" + csv(DEMO), {"999901"})) == 1


def test_a_file_with_only_a_header_has_no_rows() -> None:
    assert parse_bhavcopy(csv(), {"999901"}) == []


def test_blank_lines_and_short_rows_are_skipped() -> None:
    body = csv("", "999901,short", DEMO)
    assert len(parse_bhavcopy(body, {"999901"})) == 1


@pytest.mark.parametrize(
    "fields",
    [
        {"OpnPric": "abc"},
        {"HghPric": ""},
        {"LwPric": "0"},
        {"ClsPric": "-5"},
        {"PrvsClsgPric": "NaN"},
        {"ClsPric": "Infinity"},
        {"TtlTradgVol": "many"},
        {"TtlTradgVol": "-1"},
        {"TradDt": "28/09/2026"},
    ],
    ids=[
        "open",
        "empty-high",
        "zero-low",
        "negative-close",
        "nan",
        "inf",
        "volume",
        "neg-vol",
        "date",
    ],
)
def test_a_row_with_a_bad_number_or_date_is_skipped_and_the_rest_kept(
    fields: dict[str, str],
) -> None:
    body = csv(row(**fields), row("999902"))
    assert [p.bse_code for p in parse_bhavcopy(body, {"999901", "999902"})] == ["999902"]


def test_a_volume_written_with_decimals_is_read() -> None:
    [price] = parse_bhavcopy(csv(row(TtlTradgVol="1500.00")), {"999901"})
    assert price.volume == 1500


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"<html><body>Not Acceptable</body></html>",
        b"a,b,c\n1,2,3\n",
        HEADER.replace("ClsPric", "Close").encode() + b"\n",
        b"\xff\xfe\x00binary",
    ],
    ids=["empty", "html", "other-csv", "missing-column", "binary"],
)
def test_a_body_that_is_not_this_file_is_refused_clearly(body: bytes) -> None:
    with pytest.raises(BadBhavcopy, match="not BSE's daily price file"):
        parse_bhavcopy(body, {"999901"})


# --- the address --------------------------------------------------------------------------------


def test_the_file_address_is_on_the_allow_list() -> None:
    assert is_bhavcopy_url(bhavcopy_url(DAY))


@pytest.mark.parametrize(
    "url",
    [
        "http://www.bseindia.com/download/BhavCopy/Equity/x.CSV",
        "https://bseindia.com/x",
        "https://www.bseindia.com.evil.test/x",
        "https://www.bseindia.com:8443/x",
        "https://user@www.bseindia.com/x",
        "https://evil.test/www.bseindia.com",
        "",
    ],
)
def test_other_addresses_are_refused(url: str) -> None:
    assert not is_bhavcopy_url(url)


# --- fetching -----------------------------------------------------------------------------------


def client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


async def test_a_200_csv_is_fetched_and_parsed_with_our_user_agent() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=csv(DEMO, row("999902")))

    async with client(httpx.MockTransport(handle)) as http:
        outcome, prices = await fetch_day(http, DAY, codes={"999901"})

    assert outcome == "fetched"
    assert [p.bse_code for p in prices] == ["999901"]
    assert str(seen[0].url) == bhavcopy_url(DAY)
    assert seen[0].headers["User-Agent"] == USER_AGENT


async def test_a_file_without_our_stocks_is_still_fetched() -> None:
    async with client(httpx.MockTransport(lambda r: httpx.Response(200, content=csv(DEMO)))) as h:
        assert await fetch_day(h, DAY, codes={"111111"}) == ("fetched", [])


@pytest.mark.parametrize("status", [404, 406])
async def test_a_404_or_406_means_slow_down(status: int) -> None:
    async with client(
        httpx.MockTransport(
            lambda r: httpx.Response(status, content=b"<html>Not Acceptable</html>")
        )
    ) as http:
        assert await fetch_day(http, DAY, codes={"999901"}) == ("slow_down", [])


async def test_an_html_page_with_status_200_means_slow_down() -> None:
    async with client(
        httpx.MockTransport(lambda r: httpx.Response(200, content=b"<html>Sorry</html>"))
    ) as http:
        assert await fetch_day(http, DAY, codes={"999901"}) == ("slow_down", [])


@pytest.mark.parametrize("status", [500, 503, 403])
async def test_any_other_status_raises_so_the_job_retries(status: int) -> None:
    async with client(httpx.MockTransport(lambda r: httpx.Response(status))) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_day(http, DAY, codes={"999901"})


async def test_a_redirect_off_the_allow_list_is_refused_before_it_is_requested() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://evil.test/file.csv"})

    async with client(httpx.MockTransport(handle)) as http:
        with pytest.raises(FetchRefused):
            await fetch_day(http, DAY, codes={"999901"})
    assert seen == [bhavcopy_url(DAY)]


async def test_a_body_over_the_size_limit_is_refused() -> None:
    from app.polite_fetch import FetchTooLarge

    big = csv(DEMO) + b"x" * (6 * 1024 * 1024)
    async with client(httpx.MockTransport(lambda r: httpx.Response(200, content=big))) as http:
        with pytest.raises(FetchTooLarge):
            await fetch_day(http, DAY, codes={"999901"})
