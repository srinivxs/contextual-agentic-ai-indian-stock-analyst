"""A synthetic screener.in-style company page for the fictional DemoCo (ADR 018).

Only the structure matters: the documents section's markup, as screener.in used it when this was
written (September 2026). Real pages never enter the repository; neither does any real number.
The page deliberately mixes links that must be followed with links that must NOT be: company
websites, plain http, other BSE paths, a look-alike host, and a javascript: link.
"""

# Every filing has its own id, as on BSE: an id shared by two links would be one file (and deduped).
UUIDS = [f"0000000{n}-aaaa-4bbb-8ccc-00000000000{n}" for n in range(10)]


def transcript_url(n: int) -> str:
    return f"https://www.bseindia.com/stockinfo/AnnPdfOpen.aspx?Pname={UUIDS[n]}.pdf"


def annual_report_url(n: int) -> str:
    return f"https://www.bseindia.com/xml-data/corpfiling/AttachHis/{UUIDS[n]}.pdf"


NSE_REPORT = "https://archives.nseindia.com/annual_reports/DEMOCO_2024.pdf"

DEMOCO_PAGE = f"""<!doctype html>
<html><body>
<section id="top"><h1>DemoCo Ltd</h1></section>
<section id="documents">
  <div class="documents flex-column">
    <h3>Announcements</h3>
    <ul class="list-links">
      <li><a href="{transcript_url(9)}">Press release - DemoCo opens a widget plant</a></li>
    </ul>
  </div>
  <div class="documents annual-reports flex-column">
    <h3>Annual reports</h3>
    <ul class="list-links">
      <li><a href="{annual_report_url(0)}">Financial Year 2026<div>from bse</div></a></li>
      <li><a href="{annual_report_url(8)}">Financial Year 2025<div>from bse</div></a></li>
      <li><a href="{NSE_REPORT}">Financial Year 2024<div>from nse</div></a></li>
    </ul>
  </div>
  <div class="documents concalls flex-column">
    <h3>Concalls</h3>
    <ul class="list-links">
      <li><div>Jul 2026</div>
        <a href="{transcript_url(1)}">Transcript</a>
        <a href="https://www.democo.example/ir/q1.pdf">PPT</a>
        <a href="https://www.youtube.com/watch?v=demo">REC</a></li>
      <li><div>Apr 2026</div><a href="https://www.democo.example/ir/q4.pdf">Transcript</a></li>
      <li><div>Jan 2026</div><a href="{transcript_url(2)}">Transcript</a></li>
      <li><div>Nov 2025</div><a href="http://www.bseindia.com/stockinfo/AnnPdfOpen.aspx?Pname={UUIDS[3]}.pdf">Transcript</a></li>
      <li><div>Oct 2025</div><a href="{transcript_url(4)}">Transcript</a></li>
      <li><div>Sep 2025</div><a href="https://www.bseindia.com.evil.example/stockinfo/AnnPdfOpen.aspx?Pname={UUIDS[5]}.pdf">Transcript</a></li>
      <li><div>Aug 2025</div><a href="javascript:alert(1)">Transcript</a></li>
      <li><div>Jul 2025</div><a href="{transcript_url(6)}">Transcript</a></li>
      <li><div>Apr 2025</div><a href="{transcript_url(7)}">Transcript</a></li>
      <li><div>Apr 2025</div><a href="{transcript_url(7)}">Transcript</a></li>
    </ul>
  </div>
</section>
</body></html>
"""


def file_url(n: int) -> str:
    """The direct file address BSE serves a filing from (and AnnPdfOpen redirects to)."""
    return f"https://www.bseindia.com/xml-data/corpfiling/AttachHis/{UUIDS[n]}.pdf"


def live_url(n: int) -> str:
    return f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{UUIDS[n]}.pdf"


# What the selection must produce from DEMOCO_PAGE: the four newest BSE-hosted transcripts, in page
# order (newest first), then the newest BSE-hosted annual report. Transcripts are listed on the
# page through BSE's AnnPdfOpen.aspx script, which redirects to the file at AttachHis/<same id>.pdf
# and in the first real run often answered 406 instead; so the DIRECT file address is what is kept.
EXPECTED = [
    ("transcript", "Jul 2026", file_url(1)),
    ("transcript", "Jan 2026", file_url(2)),
    ("transcript", "Oct 2025", file_url(4)),
    ("transcript", "Jul 2025", file_url(6)),
    ("annual_report", "Financial Year 2026", annual_report_url(0)),
]
