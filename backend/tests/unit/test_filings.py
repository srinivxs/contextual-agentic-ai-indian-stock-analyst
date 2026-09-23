"""Finding a stock's official filings on its screener.in page (ADR 018).

The page is only a signpost: we read the links in its documents section and keep the ones that
point at a PDF on www.bseindia.com, the exchange where companies file. Nothing else on the page is
read or kept. The rules below are what keep that narrow.
"""

import pytest

from app.filings import (
    FilingLink,
    clean_label,
    file_candidates,
    is_official_pdf_url,
    parse_documents,
    select_filings,
    title_for,
)
from tests.filings_html import (
    DEMOCO_PAGE,
    EXPECTED,
    TODAY,
    UUIDS,
    annual_report_url,
    file_url,
    live_url,
    transcript_url,
)


def test_every_bse_filing_of_the_last_three_years_is_chosen() -> None:
    chosen = select_filings(parse_documents(DEMOCO_PAGE), today=TODAY, years=3)
    assert [(f.kind, f.label, f.url) for f in chosen] == EXPECTED


def test_a_shorter_window_keeps_only_the_newer_filings() -> None:
    chosen = select_filings(parse_documents(DEMOCO_PAGE), today=TODAY, years=1)
    assert [(f.kind, f.label) for f in chosen] == [
        ("transcript", "Jul 2026"),
        ("transcript", "Jan 2026"),
        ("transcript", "Oct 2025"),
        ("presentation", "Jul 2026"),
        ("annual_report", "Financial Year 2026"),
        ("announcement", "Press release - DemoCo opens a widget plant"),
    ]


@pytest.mark.parametrize("label", ["", "Soon", "Jul", "2026", "Foo 2026"])
def test_an_earnings_call_without_a_readable_month_is_left_out(label: str) -> None:
    link = FilingLink("transcript", label, transcript_url(1))
    assert select_filings([link], today=TODAY, years=3) == []


def test_nothing_hosted_off_bse_is_chosen() -> None:
    urls = {f.url for f in select_filings(parse_documents(DEMOCO_PAGE), today=TODAY, years=3)}
    assert not any("democo.example" in url for url in urls)  # company-site newsletter and filings
    assert not any("nseindia" in url for url in urls)  # the NSE-hosted annual report


def test_a_page_without_a_documents_section_gives_nothing() -> None:
    assert parse_documents("<html><body><h1>Not found</h1></body></html>") == []
    assert select_filings([], today=TODAY, years=3) == []


def test_the_same_link_listed_twice_is_chosen_once() -> None:
    links = [FilingLink("transcript", "Apr 2025", transcript_url(7))] * 2
    assert len(select_filings(links, today=TODAY, years=3)) == 1


@pytest.mark.parametrize(
    "url",
    [
        transcript_url(1),
        annual_report_url(0),
        f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{UUIDS[2]}.pdf",
    ],
)
def test_the_two_bse_filing_addresses_are_official(url: str) -> None:
    assert is_official_pdf_url(url)


@pytest.mark.parametrize(
    "url",
    [
        f"http://www.bseindia.com/stockinfo/AnnPdfOpen.aspx?Pname={UUIDS[1]}.pdf",  # not https
        f"https://www.bseindia.com.evil.example/stockinfo/AnnPdfOpen.aspx?Pname={UUIDS[1]}.pdf",
        f"https://www.bseindia.com@evil.example/stockinfo/AnnPdfOpen.aspx?Pname={UUIDS[1]}.pdf",
        f"https://bseindia.com/stockinfo/AnnPdfOpen.aspx?Pname={UUIDS[1]}.pdf",  # other host
        f"https://www.bseindia.com:8443/stockinfo/AnnPdfOpen.aspx?Pname={UUIDS[1]}.pdf",
        f"https://www.bseindia.com/stockinfo/AnnPdfOpen.aspx?Pname={UUIDS[1]}.pdf&x=1",
        "https://www.bseindia.com/stockinfo/AnnPdfOpen.aspx?Pname=../../etc/passwd",
        f"https://www.bseindia.com/xml-data/corpfiling/AttachHis/../{UUIDS[0]}.pdf",
        f"https://www.bseindia.com/xml-data/corpfiling/Other/{UUIDS[0]}.pdf",
        # A filing path buried after some other path on the right host: an open redirect, say.
        f"https://www.bseindia.com/redirect?to=/xml-data/corpfiling/AttachHis/{UUIDS[0]}.pdf",
        f"https://www.bseindia.com/other/stockinfo/AnnPdfOpen.aspx?Pname={UUIDS[1]}.pdf",
        "https://www.bseindia.com/",
        "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w",
        "https://www.screener.in/company/source/quarter/1/",  # disallowed by screener's robots.txt
        "javascript:alert(1)",
        "",
    ],
)
def test_anything_else_is_not_official(url: str) -> None:
    assert not is_official_pdf_url(url)


def test_titles_name_the_stock_the_kind_and_the_period() -> None:
    assert title_for("TCS", FilingLink("transcript", "Jul 2026", transcript_url(1))) == (
        "TCS earnings call transcript, Jul 2026"
    )
    assert title_for("TCS", FilingLink("annual_report", "Financial Year 2026", "x")) == (
        "TCS annual report, Financial Year 2026"
    )


def test_a_label_is_cleaned_and_cut_so_a_title_always_fits() -> None:
    long_label = "  Jul\n 2026 " + "x" * 400
    title = title_for("HDFCBANK", FilingLink("transcript", long_label, transcript_url(1)))
    assert "\n" not in title
    assert 1 <= len(title) <= 200
    assert title.startswith("HDFCBANK earnings call transcript, Jul 2026")


def test_self_closing_tags_inside_the_section_do_not_confuse_the_parser() -> None:
    page = (
        '<div id="documents"><br><div class="documents annual-reports"><img src="x.png">'
        f'<ul class="list-links"><li><a href="{annual_report_url(0)}">Financial Year<br/> 2026'
        "<div>from bse</div></a></li></ul></div></div>"
        '<div class="concalls"><a href="https://www.bseindia.com/x">Transcript</a></div>'
    )
    links = parse_documents(page)
    assert links == [FilingLink("annual_report", "Financial Year 2026", annual_report_url(0))]


def test_a_script_page_link_becomes_the_direct_file_it_redirects_to() -> None:
    """AnnPdfOpen.aspx?Pname=<id>.pdf redirects to AttachHis/<id>.pdf: go there directly."""
    assert file_candidates(transcript_url(1)) == [file_url(1), live_url(1)]


def test_a_historical_file_is_tried_first_and_the_live_folder_second() -> None:
    assert file_candidates(annual_report_url(0)) == [annual_report_url(0), live_url(0)]


def test_a_live_file_is_tried_first_and_the_historical_folder_second() -> None:
    assert file_candidates(live_url(3)) == [live_url(3), annual_report_url(3)]


def test_every_candidate_is_itself_an_official_address() -> None:
    for url in (transcript_url(1), annual_report_url(0), live_url(3)):
        assert all(is_official_pdf_url(candidate) for candidate in file_candidates(url))


def test_only_an_official_address_has_candidates() -> None:
    assert file_candidates("https://evil.example/x.pdf") == []


def test_presentations_and_announcements_have_their_own_titles() -> None:
    assert title_for("TCS", FilingLink("presentation", "Jul 2026", "x")) == (
        "TCS investor presentation, Jul 2026"
    )
    assert title_for("TCS", FilingLink("announcement", "Press release - results", "x")) == (
        "TCS announcement, Press release - results"
    )


def test_the_announcements_list_is_read_but_not_the_credit_ratings() -> None:
    page = (
        '<div id="documents"><div class="documents credit-ratings"><ul class="list-links">'
        f'<li><a href="{transcript_url(15)}">Rating update<div>from crisil</div></a></li>'
        "</ul></div></div>"
    )
    assert parse_documents(page) == []


def test_doubled_apostrophes_from_the_source_text_are_made_single() -> None:
    """BSE subjects arrive with '' where one ' was meant ("Investors'' Meeting")."""
    link = FilingLink("announcement", "Update On Institutional Investors'' Meeting", "x")
    assert (
        title_for("RELIANCE", link)
        == "RELIANCE announcement, Update On Institutional Investors' Meeting"
    )
    assert clean_label(link.label) == "Update On Institutional Investors' Meeting"
