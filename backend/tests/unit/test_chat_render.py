"""Turning checked claims into the answer text and its numbered sources (P12)."""

from dataclasses import replace

import pytest

from app.chat.answer_check import Claim
from app.chat.contract import Source
from app.chat.evidence import EvidenceItem
from app.chat.render import disclosures, render

BSE = "https://www.bseindia.com/xml-data/corpfiling/AttachHis/demo.pdf"

FACT = EvidenceItem(
    id="F1",
    kind="fact",
    symbol="TCS",
    text="TCS · Net profit · FY2026 · consolidated · ₹1,234 crore",
    source="filing",
    label="Annual report · Annual Report 2026 · p.37",
    url=f"{BSE}#page=37",
    quote="Net profit for the year was 1,234 crore.",
)
SCREENER = EvidenceItem(
    id="F2",
    kind="fact",
    symbol="TCS",
    text="TCS · Net profit · FY2025 · consolidated · ₹1,111 crore",
    source="screener",
    label="screener.in · profit-loss · Net Profit · Mar 2025",
    url="https://www.screener.in/company/TCS/consolidated/",
    quote=None,
)
DERIVED = EvidenceItem(
    id="D1",
    kind="derived",
    symbol="TCS",
    text="TCS · Net profit growth · 11.1% · Change in net profit from FY2025 to FY2026.",
    source="derived",
    label="Computed: Net profit growth",
    url=None,
    quote="Change in net profit from FY2025 to FY2026.",
)
PASSAGE = EvidenceItem(
    id="N1",
    kind="passage",
    symbol="TCS",
    text="TCS · Earnings call · Jul 2026 · p.7: DemoCo said demand held up.",
    source="filing",
    label="Earnings call · Jul 2026 · p.7",
    url=f"{BSE}#page=7",
    quote="word " * 100,  # longer than an excerpt
)
EVIDENCE = [FACT, SCREENER, DERIVED, PASSAGE]


def test_markers_are_numbered_in_order_of_first_use_and_reused() -> None:
    text, sources = render(
        [
            Claim("Net profit was ₹1,234 crore in FY2026.", ("D1", "F1")),
            Claim("It was ₹1,111 crore a year earlier.", ("F2", "F1")),
            Claim("That is 11.1% growth.", ("D1",)),
        ],
        EVIDENCE,
    )
    assert text == (
        "Net profit was ₹1,234 crore in FY2026. [1][2] "
        "It was ₹1,111 crore a year earlier. [3][2] "
        "That is 11.1% growth. [1]"
    )
    assert sources == (
        Source(
            marker=1,
            source="derived",
            label="Computed: Net profit growth",
            url=None,
            quote="Change in net profit from FY2025 to FY2026.",
        ),
        Source(
            marker=2,
            source="filing",
            label="Annual report · Annual Report 2026 · p.37",
            url=f"{BSE}#page=37",
            quote="Net profit for the year was 1,234 crore.",
        ),
        Source(
            marker=3,
            source="screener",
            label="screener.in · profit-loss · Net Profit · Mar 2025",
            url="https://www.screener.in/company/TCS/consolidated/",
            quote=None,
        ),
    )


def test_a_quote_is_cut_to_an_excerpt() -> None:
    _, (source,) = render([Claim("Demand held up.", ("N1",))], EVIDENCE)
    assert source.quote is not None
    assert len(source.quote) <= 301
    assert source.quote.endswith("…")


def test_ids_the_model_wrote_into_its_text_are_replaced_by_markers() -> None:
    text, _ = render([Claim("Net profit rose [F1] to ₹1,234 crore [F1].", ("F1", "F1"))], EVIDENCE)
    assert text == "Net profit rose to ₹1,234 crore. [1]"


def test_rendering_is_deterministic() -> None:
    claims = [Claim("A.", ("N1", "F1")), Claim("B.", ("F2",))]
    assert render(claims, EVIDENCE) == render(claims, EVIDENCE)


def test_an_empty_answer_renders_as_nothing() -> None:
    assert render([], EVIDENCE) == ("", ())


def test_rendering_an_unchecked_answer_with_an_unknown_id_is_refused() -> None:
    with pytest.raises(ValueError, match="checked"):
        render([Claim("A.", ("F9",))], EVIDENCE)


# --- disclosing another source's figure (the owner's review, 2026-09-29) -------------------------

MAIN = EvidenceItem(
    id="F1",
    kind="fact",
    symbol="RELIANCE",
    text="RELIANCE · Revenue from operations · FY2024 · consolidated · ₹914 crore",
    source="filing",
    label="Annual report · Annual Report 2024 · p.133",
    url=f"{BSE}#page=133",
    quote="Revenue from Operations 914",
    metric="revenue_from_operations",
    period="FY2024",
    amount="₹914 crore",
    rivals=("F2",),
)
RIVAL = EvidenceItem(
    id="F2",
    kind="fact",
    symbol="RELIANCE",
    text="RELIANCE · Revenue from operations · FY2024 · consolidated · ₹899 crore · screener.in",
    source="screener",
    label="screener.in · profit-loss · Sales · Mar 2024",
    url="https://www.screener.in/company/RELIANCE/consolidated/",
    quote=None,
    metric="revenue_from_operations",
    period="FY2024",
    amount="₹899 crore",
)


def test_a_cited_figure_another_source_disputes_is_disclosed_with_that_source() -> None:
    claims = [Claim("Revenue was ₹914 crore.", ("F1",))]
    disclosure = (
        "screener.in gives ₹899 crore for FY2024, against ₹914 crore in the annual report; the "
        "stored data does not establish that the two measure the same thing, so they are not "
        "treated as interchangeable."
    )
    assert disclosures(claims, [MAIN, RIVAL]) == [Claim(disclosure, ("F2",))]
    text, sources = render([*claims, *disclosures(claims, [MAIN, RIVAL])], [MAIN, RIVAL])
    assert text == f"Revenue was ₹914 crore. [1] {disclosure} [2]"
    assert [s.label for s in sources] == [MAIN.label, RIVAL.label]


def test_nothing_is_added_when_the_answer_already_cites_the_other_source() -> None:
    claims = [Claim("The annual report gives ₹914 crore, screener.in ₹899 crore.", ("F1", "F2"))]
    assert disclosures(claims, [MAIN, RIVAL]) == []
    assert disclosures([Claim("Other.", ("F2",))], [MAIN, RIVAL]) == []


def test_a_disclosure_names_what_each_source_calls_its_figure() -> None:
    main = replace(MAIN, reported_as="Revenue from Operations")
    rival = replace(RIVAL, reported_as="Sales")
    [added] = disclosures([Claim("Revenue was ₹914 crore.", ("F1",))], [main, rival])
    assert added.text == (
        'screener.in gives ₹899 crore for FY2024 (reported as "Sales"), against ₹914 crore in '
        'the annual report (reported as "Revenue from Operations"); the stored data does not '
        "establish that the two measure the same thing, so they are not treated as "
        "interchangeable."
    )
