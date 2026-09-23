"""Tiny synthetic PDFs, written byte by byte, for tests.

Real company filings never enter the repository (the project notes), so the tests build their own documents
about the fictional DemoCo. Each page is a list of lines drawn in Helvetica; an empty list makes a
page with no text at all, which is what a scanned page looks like to a text extractor.
"""


def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]]) -> bytes:
    """A valid PDF 1.7 file with one page per entry of ``pages``."""
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    contents = []
    for lines in pages:
        operations = ["BT /F1 11 Tf 72 740 Td 14 TL"]
        operations += [f"({_escape(line)}) Tj T*" for line in lines]
        operations.append("ET")
        stream = "\n".join(operations).encode("latin-1")
        contents.append(add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"))

    pages_id = len(objects) + len(contents) + 1  # the page tree comes right after the pages
    page_ids = [
        add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (pages_id, font, content)
        )
        for content in contents
    ]
    kids = b" ".join(b"%d 0 R" % page for page in page_ids)
    add(b"<< /Type /Pages /Kids [%s] /Count %d >>" % (kids, len(page_ids)))
    catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog,
        xref,
    )
    return bytes(out)


# A two-page results announcement for the fictional DemoCo. Never real numbers for a real stock.
DEMOCO_RESULTS = make_pdf(
    [
        [
            "DemoCo Limited (fictional) - Unaudited results for the quarter ended 30 June",
            "Revenue from operations for the quarter was Rs 1,234 crore.",
            "Net profit for the quarter was Rs 210 crore.",
        ],
        [
            "Segment information",
            "The widgets segment grew 12 percent year on year.",
        ],
    ]
)

# What a scanned document looks like to a text extractor: pages, but no text on any of them.
SCANNED = make_pdf([[], [], []])

# The signature is right, the rest is not a PDF.
CORRUPT = b"%PDF-1.7\nthis is not really a pdf\n"
