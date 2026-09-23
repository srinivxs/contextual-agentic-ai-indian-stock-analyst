"""Turning a PDF into text, one entry per page (pypdfium2, the PDFium engine)."""

import pytest

from app.pdf_text import UnreadablePdf, extract_pages
from tests.pdfs import CORRUPT, DEMOCO_RESULTS, SCANNED, make_pdf


def test_each_page_becomes_one_entry_in_order() -> None:
    pages = extract_pages(DEMOCO_RESULTS)

    assert len(pages) == 2
    assert pages[0].startswith("DemoCo Limited (fictional)")
    assert "Rs 1,234 crore" in pages[0]
    assert pages[1] == "Segment information\nThe widgets segment grew 12 percent year on year."


def test_line_endings_and_spacing_are_normalised() -> None:
    """PDFium returns Windows line endings and layout spaces; quotes must match plain text later."""
    [page] = extract_pages(make_pdf([["  Revenue    was   Rs 5 crore  ", "next line"]]))

    # PDFium emits nothing for a line without glyphs, so blank lines never reach us from a PDF
    # like this one; the normaliser's blank-line rule is exercised on plain text below.
    assert "\r" not in page
    assert page == "Revenue was Rs 5 crore\nnext line"


def test_runs_of_blank_lines_collapse_to_one_paragraph_break() -> None:
    from app.pdf_text import _normalise

    assert _normalise("a\r\n\r\n\r\n\r\nb  \t c\r\n") == "a\n\nb c"


def test_a_page_without_text_is_an_empty_string_not_a_missing_page() -> None:
    """Page numbers must stay true: an image-only slide is still page 2."""
    pages = extract_pages(make_pdf([["first"], [], ["third"]]))
    assert pages == ["first", "", "third"]


def test_a_scanned_document_yields_pages_with_no_text() -> None:
    assert extract_pages(SCANNED) == ["", "", ""]


def test_characters_postgres_cannot_store_are_removed() -> None:
    """A NUL byte in extracted text would make the whole INSERT fail."""
    [page] = extract_pages(make_pdf([["before\x00after"]]))
    assert "\x00" not in page
    assert "before" in page
    assert "after" in page


def test_a_corrupt_file_is_reported_as_unreadable() -> None:
    with pytest.raises(UnreadablePdf):
        extract_pages(CORRUPT)
