"""The RBI feed reader (P15): parsing is lenient but never trusts the feed; fetching is polite."""

import hashlib
import time
from datetime import UTC, datetime

import httpx
import pytest

from app.feeds.model import MAX_SUMMARY_CHARS, RBI_FEED_URL
from app.feeds.rbi import (
    MAX_ITEMS,
    MAX_TITLE_CHARS,
    canonical_url,
    fetch_feed,
    fixture_items,
    parse_feed,
    title_hash,
)
from app.polite_fetch import USER_AGENT, FetchRefused, FetchTooLarge

LINK = "https://www.rbi.org.in/scripts/BS_PressReleaseDisplay.aspx?prid=1"


def rss(*items: str) -> bytes:
    body = '<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel><title>T</title>'
    return (body + "".join(items) + "</channel></rss>").encode()


def item(
    title: str | None = "A title",
    link: str | None = LINK,
    date: str | None = "Tue, 29 Sep 2026 07:50:00 GMT",
    description: str | None = "Some text",
) -> str:
    parts = []
    if title is not None:
        parts.append(f"<title>{title}</title>")
    if description is not None:
        parts.append(f"<description>{description}</description>")
    if link is not None:
        parts.append(f"<link>{link}</link>")
    if date is not None:
        parts.append(f"<pubDate>{date}</pubDate>")
    return "<item>" + "".join(parts) + "</item>"


# ---------- canonical_url ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (LINK, LINK),
        ("http://www.rbi.org.in/a?x=1", "https://www.rbi.org.in/a?x=1"),
        ("HTTPS://WWW.RBI.ORG.IN/a", "https://www.rbi.org.in/a"),
        ("https://rbi.org.in/a", "https://rbi.org.in/a"),
        ("https://www.rbi.org.in/a#top", "https://www.rbi.org.in/a"),
        ("  https://www.rbi.org.in/a  ", "https://www.rbi.org.in/a"),
        (
            "https://www.rbi.org.in/a?prid=5&utm_source=x&UTM_medium=y&fbclid=z&gclid=q",
            "https://www.rbi.org.in/a?prid=5",
        ),
        ("https://www.rbi.org.in/a?utm_source=x", "https://www.rbi.org.in/a"),
        ("https://www.rbi.org.in:443/a", "https://www.rbi.org.in/a"),
    ],
)
def test_canonical_url_cleans_allowed_addresses(raw: str, expected: str) -> None:
    assert canonical_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "https://evil.example/a",
        "https://www.rbi.org.in.evil.example/a",
        "https://evilrbi.org.in/a",
        "https://user@www.rbi.org.in/a",
        "https://www.rbi.org.in:8443/a",
        "javascript:alert(1)",
        "ftp://www.rbi.org.in/a",
        "//www.rbi.org.in/a",
        "/scripts/a",
        "",
        "   ",
        "https://",
        "https://www.rbi.org.in:notaport/a",
        "http://[::1",
    ],
)
def test_canonical_url_refuses_everything_else(raw: str) -> None:
    assert canonical_url(raw) is None


# ---------- title_hash ----------


def test_title_hash_ignores_case_and_spacing() -> None:
    expected = hashlib.sha256(b"a b c").hexdigest()
    assert title_hash("  A   B\n\tC ") == expected
    assert title_hash("a b c") == expected


def test_title_hash_differs_for_different_titles() -> None:
    assert title_hash("one") != title_hash("two")


# ---------- parse_feed ----------


def test_a_well_formed_item_is_read() -> None:
    [it] = parse_feed(rss(item()))
    assert it.canonical_url == LINK
    assert it.title == "A title"
    assert it.title_hash == title_hash("A title")
    assert it.published_at == datetime(2026, 9, 29, 7, 50, tzinfo=UTC)
    assert it.summary == "Some text"
    assert it.is_fixture is False


def test_the_fixture_flag_is_passed_through() -> None:
    [it] = parse_feed(rss(item()), is_fixture=True)
    assert it.is_fixture is True


def test_a_byte_order_mark_is_tolerated() -> None:
    [it] = parse_feed(b"\xef\xbb\xbf" + rss(item()))
    assert it.title == "A title"


def test_cdata_html_and_entities_become_plain_text() -> None:
    real_shape = (
        "<![CDATA[<table><tr><td>Notified Amount (in &#8377; crore)</td>"
        '<td align="right">1,00,000</td></tr></table>'
        "<p>Deputy&nbsp;General Manager &amp; team</p>]]>"
    )
    title = "<![CDATA[Result of the auction &amp; more]]>"
    [it] = parse_feed(rss(item(title=title, description=real_shape)))
    assert it.title == "Result of the auction & more"
    assert "<" not in it.summary
    assert "₹ crore" in it.summary
    assert "1,00,000" in it.summary
    assert "Deputy General Manager & team" in it.summary
    assert "  " not in it.summary


def test_escaped_markup_in_a_plain_description_is_stripped() -> None:
    [it] = parse_feed(rss(item(description="&lt;p&gt;Hello &amp;amp; bye&lt;/p&gt;")))
    assert it.summary == "Hello & bye"


def test_missing_description_gives_an_empty_summary() -> None:
    [it] = parse_feed(rss(item(description=None)))
    assert it.summary == ""


def test_missing_date_is_none() -> None:
    [it] = parse_feed(rss(item(date=None)))
    assert it.published_at is None


@pytest.mark.parametrize("bad", ["not a date", "", "Tue, 99 Zzz 2026 99:99:99 GMT"])
def test_unreadable_dates_are_none(bad: str) -> None:
    [it] = parse_feed(rss(item(date=bad)))
    assert it.published_at is None


def test_a_date_with_an_offset_is_converted_to_utc() -> None:
    [it] = parse_feed(rss(item(date="Tue, 29 Sep 2026 13:20:00 +0530")))
    assert it.published_at == datetime(2026, 9, 29, 7, 50, tzinfo=UTC)


def test_a_date_without_a_zone_is_taken_as_utc() -> None:
    [it] = parse_feed(rss(item(date="29 Sep 2026 07:50:00 -0000")))
    assert it.published_at == datetime(2026, 9, 29, 7, 50, tzinfo=UTC)


def test_naive_dates_are_taken_as_utc() -> None:
    [it] = parse_feed(rss(item(date="Tue, 29 Sep 2026 07:50:00")))
    assert it.published_at == datetime(2026, 9, 29, 7, 50, tzinfo=UTC)


def test_items_without_a_title_or_an_allowed_link_are_skipped() -> None:
    body = rss(
        item(title=None),
        item(title="   "),
        item(link=None),
        item(link="https://evil.example/a"),
        item(link="javascript:alert(1)"),
        item(title="Kept"),
    )
    assert [i.title for i in parse_feed(body)] == ["Kept"]


def test_duplicates_by_canonical_url_are_kept_once() -> None:
    body = rss(
        item(title="First", link=LINK),
        item(title="Second", link=LINK + "&utm_source=x#frag"),
        item(title="Third", link="https://www.rbi.org.in/other"),
    )
    assert [i.title for i in parse_feed(body)] == ["First", "Third"]


def test_a_long_summary_is_cut_at_a_word_boundary() -> None:
    text = " ".join(["word"] * 400)
    [it] = parse_feed(rss(item(description=text)))
    assert len(it.summary) <= MAX_SUMMARY_CHARS
    assert len(it.summary) > MAX_SUMMARY_CHARS - 10
    assert it.summary.endswith("word")
    assert text.startswith(it.summary)


def test_a_summary_cut_exactly_at_a_word_end_keeps_the_whole_word() -> None:
    text = "x" * (MAX_SUMMARY_CHARS - 1) + " tail"
    [it] = parse_feed(rss(item(description=text)))
    assert it.summary == "x" * (MAX_SUMMARY_CHARS - 1)


def test_a_long_summary_without_spaces_is_cut_hard() -> None:
    [it] = parse_feed(rss(item(description="x" * 5000)))
    assert it.summary == "x" * MAX_SUMMARY_CHARS


def test_a_summary_at_the_limit_is_untouched() -> None:
    text = "y" * MAX_SUMMARY_CHARS
    [it] = parse_feed(rss(item(description=text)))
    assert it.summary == text


def test_empty_and_garbage_bodies_give_nothing() -> None:
    assert parse_feed(b"") == []
    assert parse_feed(b"\x00\xff\xfe not xml at all") == []
    assert parse_feed(b"<html><body>Access denied</body></html>") == []


def test_malformed_xml_falls_back_to_the_regex_reader() -> None:
    body = (
        b"<rss><channel><item><title><![CDATA[Broken & bare]]></title>"
        b"<link>https://www.rbi.org.in/a</link>"
        b"<pubDate>Tue, 29 Sep 2026 07:50:00 GMT</pubDate>"
        b"<description><![CDATA[<p>Text <b>here</b></p>]]></description></item>"
        b"<item><title>Second & unclosed</title><link>https://www.rbi.org.in/b</link></item>"
        b"<item><title>No link</title></item>"
        b"<item><TITLE>Upper</TITLE><LINK>https://www.rbi.org.in/c</LINK></item>"
        b"</channel>"  # never closed: not well-formed
    )
    items = parse_feed(body)
    assert [i.title for i in items] == ["Broken & bare", "Second & unclosed", "Upper"]
    assert items[0].summary == "Text here"
    assert items[0].published_at == datetime(2026, 9, 29, 7, 50, tzinfo=UTC)
    assert items[1].published_at is None


def test_the_fallback_also_skips_foreign_links_and_duplicates() -> None:
    body = (
        b"<item><title>A</title><link>https://evil.example/x</link></item>"
        b"<item><title>B</title><link>https://www.rbi.org.in/b</link></item>"
        b"<item><title>B again</title><link>https://www.rbi.org.in/b#z</link></item>"
    )
    assert [i.title for i in parse_feed(body)] == ["B"]


def test_the_fallback_handles_non_utf8_bytes() -> None:
    body = b"<item><title>Caf\xe9 &</title><link>https://www.rbi.org.in/b</link></item>"
    [it] = parse_feed(body)
    assert it.title.startswith("Caf")


BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
]>
<rss><channel><item><title>&lol2;</title><link>https://www.rbi.org.in/a</link></item></channel></rss>"""


def test_doctype_and_entity_declarations_are_never_expanded() -> None:
    items = parse_feed(BILLION_LAUGHS)
    # the fallback reads the item but never expands the entity
    assert [i.title for i in items] == ["&lol2;"]
    assert "lol" not in items[0].title.replace("&lol2;", "")


def test_a_lowercase_doctype_is_refused_too() -> None:
    body = b'<!doctype x [<!entity e "boom">]><rss><item><title>&e;</title>'
    body += b"<link>https://www.rbi.org.in/a</link></item></rss>"
    assert [i.title for i in parse_feed(body)] == ["&e;"]


def test_an_entity_declaration_without_doctype_is_refused_too() -> None:
    body = b'<!ENTITY e "boom"><rss><item><title>&e;</title>'
    body += b"<link>https://www.rbi.org.in/a</link></item></rss>"
    assert [i.title for i in parse_feed(body)] == ["&e;"]


def test_an_external_entity_is_not_fetched_or_read() -> None:
    body = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
        b"<rss><item><title>&e;</title><link>https://www.rbi.org.in/a</link></item></rss>"
    )
    assert [i.title for i in parse_feed(body)] == ["&e;"]


# ---------- fetch_feed ----------


def client(handler: httpx.MockTransport) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=handler)


async def test_the_first_fetch_sends_no_validators_and_returns_the_new_ones() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            content=b"<rss/>",
            headers={"ETag": 'W/"abc"', "Last-Modified": "Tue, 29 Sep 2026 07:50:54 GMT"},
        )

    async with client(httpx.MockTransport(handler)) as http:
        result = await fetch_feed(http, etag=None, last_modified=None)

    assert result.status == "ok"
    assert result.body == b"<rss/>"
    assert result.etag == 'W/"abc"'
    assert result.last_modified == "Tue, 29 Sep 2026 07:50:54 GMT"
    assert str(seen[0].url) == RBI_FEED_URL
    assert seen[0].headers["user-agent"] == USER_AGENT
    assert "if-none-match" not in seen[0].headers
    assert "if-modified-since" not in seen[0].headers


async def test_known_validators_are_sent_back() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"<rss/>")

    async with client(httpx.MockTransport(handler)) as http:
        result = await fetch_feed(
            http, etag='W/"abc"', last_modified="Tue, 29 Sep 2026 07:50:54 GMT"
        )

    assert seen[0].headers["if-none-match"] == 'W/"abc"'
    assert seen[0].headers["if-modified-since"] == "Tue, 29 Sep 2026 07:50:54 GMT"
    assert seen[0].headers["user-agent"] == USER_AGENT
    assert result.etag is None  # the server sent none; we do not invent one
    assert result.last_modified is None


async def test_304_means_not_modified_and_keeps_the_validators() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304)

    async with client(httpx.MockTransport(handler)) as http:
        result = await fetch_feed(http, etag='W/"abc"', last_modified="Tue, 29 Sep 2026")

    assert result.status == "not_modified"
    assert result.body == b""
    assert result.etag == 'W/"abc"'
    assert result.last_modified == "Tue, 29 Sep 2026"


async def test_a_redirect_to_another_host_is_refused_without_a_request() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://evil.example/feed.xml"})

    async with client(httpx.MockTransport(handler)) as http:
        with pytest.raises(FetchRefused):
            await fetch_feed(http, etag=None, last_modified=None)

    assert seen == [RBI_FEED_URL]


async def test_a_redirect_within_rbi_is_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.rbi.org.in" and request.url.path.endswith("rss.xml"):
            return httpx.Response(301, headers={"Location": "https://rbi.org.in/new.xml"})
        return httpx.Response(200, content=b"<rss/>")

    async with client(httpx.MockTransport(handler)) as http:
        result = await fetch_feed(http, etag=None, last_modified=None)

    assert result.body == b"<rss/>"


async def test_an_oversize_body_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))

    async with client(httpx.MockTransport(handler)) as http:
        with pytest.raises(FetchTooLarge):
            await fetch_feed(http, etag=None, last_modified=None)


async def test_a_body_of_exactly_two_megabytes_is_accepted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * (2 * 1024 * 1024))

    async with client(httpx.MockTransport(handler)) as http:
        result = await fetch_feed(http, etag=None, last_modified=None)

    assert len(result.body) == 2 * 1024 * 1024


@pytest.mark.parametrize("status", [403, 404, 500, 503])
async def test_other_statuses_raise_for_the_job_to_retry(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async with client(httpx.MockTransport(handler)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await fetch_feed(http, etag=None, last_modified=None)


# ---------- fixture_items ----------


def test_fixture_items_are_synthetic_offline_and_labelled() -> None:
    items = fixture_items()
    assert 8 <= len(items) <= 10
    assert all(i.is_fixture for i in items)
    assert all(i.title.startswith("Sample (fixture): ") for i in items)
    assert all(i.canonical_url.startswith("https://www.rbi.org.in/fixture/") for i in items)
    assert all(i.published_at is not None and i.published_at.year == 2026 for i in items)
    assert all(i.summary for i in items)
    assert len({i.canonical_url for i in items}) == len(items)
    assert len({i.title_hash for i in items}) == len(items)


def test_fixture_items_name_no_real_company() -> None:
    banned = ["reliance", "tata", "tcs", "hdfc", "infosys", "jio", "sbi", "icici"]
    for i in fixture_items():
        text = f"{i.title} {i.summary}".lower()
        assert not any(name in text for name in banned), text


# ---------- hardening (the P14/P15 security review) ----------


def test_a_summary_cut_just_before_a_space_keeps_every_character() -> None:
    text = "y" * MAX_SUMMARY_CHARS + " tail"
    [it] = parse_feed(rss(item(description=text)))
    assert it.summary == "y" * MAX_SUMMARY_CHARS


@pytest.mark.parametrize(
    "body",
    [
        b"<!DOCTYPE x>" + b"<item>" * 300_000,  # openers with no closer
        b"<!DOCTYPE x><item>" + b"<title>" * 300_000 + b"</item>",  # an unclosed field
        b"<!DOCTYPE x>" + b"<item " * 300_000,  # openers with no '>'
    ],
    ids=["no-closer", "unclosed-field", "no-bracket"],
)
def test_the_fallback_reader_stays_fast_on_hostile_bodies(body: bytes) -> None:
    """A regex reader scanning to the end of the body for every opener would take minutes on
    these; the plain string search reads each byte a bounded number of times."""
    started = time.perf_counter()
    assert parse_feed(body) == []
    assert time.perf_counter() - started < 5


def test_nul_characters_never_reach_the_database() -> None:
    body = b"<rss><channel><item><title>A\x00B</title><link>" + LINK.encode() + b"</link></item>"
    [it] = parse_feed(body)
    assert it.title == "AB"


def test_a_very_long_title_is_cut_to_what_the_table_allows() -> None:
    [it] = parse_feed(rss(item(title="t" * 2000)))
    assert len(it.title) == MAX_TITLE_CHARS


def test_an_over_long_link_is_skipped() -> None:
    assert parse_feed(rss(item(link=LINK + "&x=" + "a" * 2000))) == []


def test_at_most_max_items_are_read_from_one_body() -> None:
    items = [item(title=f"T{n}", link=f"{LINK}{n}") for n in range(MAX_ITEMS + 5)]
    assert len(parse_feed(rss(*items))) == MAX_ITEMS
    broken = rss(*items).replace(b"<channel>", b"<!DOCTYPE x><channel>")
    assert len(parse_feed(broken)) == MAX_ITEMS


def test_the_fallback_reader_does_not_take_a_longer_tag_for_an_item() -> None:
    body = (
        "<!DOCTYPE x><items><item-note>no</item-note>" + item(title="Real") + "</items>"
    ).encode()
    assert [it.title for it in parse_feed(body)] == ["Real"]
