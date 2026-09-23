"""PDF -> plain text, one string per page, using pypdfium2 (PDFium, the engine Chrome uses).

Chosen in P9 for its permissive licence (BSD-3 / Apache-2.0; PyMuPDF is AGPL), its speed on long
reports, and its reading order. The text is normalised so that a quote copied from it later (P11)
can be compared with a plain string: Unix line endings, single spaces, no characters PostgreSQL
cannot store.

This module is synchronous and CPU-bound. The worker calls it through ``asyncio.to_thread`` so it
never blocks the event loop.
"""

import re

import pypdfium2 as pdfium

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # everything but \t, \n and \r
_SPACES = re.compile("[ \t\u00a0]+")  # space, tab and no-break space, as escapes
_BLANK_LINES = re.compile(r"\n{3,}")


class UnreadablePdf(Exception):
    """PDFium could not open the file: it is damaged, encrypted, or not really a PDF."""


def _normalise(text: str) -> str:
    text = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [_SPACES.sub(" ", line).strip() for line in text.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def extract_pages(data: bytes) -> list[str]:
    """Every page's text, in order. A page with no text (an image) is an empty string."""
    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as error:
        raise UnreadablePdf(str(error)) from error
    try:
        pages = []
        for index in range(len(document)):
            page = document[index]
            textpage = page.get_textpage()
            try:
                pages.append(_normalise(textpage.get_text_range()))
            finally:
                textpage.close()
                page.close()
        return pages
    finally:
        document.close()
