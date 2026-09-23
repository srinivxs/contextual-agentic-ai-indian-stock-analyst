# 018 — Official filings found on screener.in and fetched from BSE, automatically

- **Status:** Accepted (P9c, 2026-09-23; the owner's decision). **Amends [ADR 007](007-source-strategy.md)**:
  company documents no longer depend on the owner downloading them. Uploads remain as a second door.
- **Date:** 2026-09-23

## Context

ADR 007 made hand-downloaded PDFs the source of company fundamentals, because every automated
source tested in P0 was blocked or its terms forbade our use. The owner pointed out the cost: the
data goes stale unless someone keeps downloading, which is not what an agentic assistant should need.

A re-check on 2026-09-23 found:

- BSE's announcement **listing** API still refuses honest clients (HTTP 403).
- But the **filing PDFs themselves** on `www.bseindia.com` download normally with an honest request.
- A stock's screener.in company page lists links to those PDFs (earnings-call transcripts, annual
  reports), and screener's robots.txt allows `/company/<symbol>/`. It disallows
  `/company/source/quarter/*` (its "Raw PDF" results links), which we therefore never follow.
- An open-source screener scraper (GPL-3.0, JavaScript) confirmed the page structure. We do not use
  it: we need only the links, not screener's tables, and GPL and a second runtime would come with it.

## Decision

The worker finds and fetches official filings itself (the owner chose option A of three):

1. **Timer:** once a day per stock (`FILINGS_REFRESH_HOURS=24`), a `discover_filings` job.
2. **Discover:** read the stock's screener.in company page; from its documents section keep only
   links to PDFs on `www.bseindia.com` at BSE's two filing paths; choose the **4 newest earnings-call
   transcripts and the newest annual report** (15 documents for 3 stocks); queue one `fetch_filing`
   job for each address not already stored.
3. **Fetch:** download the PDF from BSE, check it is a PDF, store it, and record it as a document
   with `source = 'bse'` and its official address, then P9's ingestion takes over unchanged.

The narrow rules that keep this defensible, each enforced in code and tested:

| Rule | Where |
|---|---|
| Off unless switched on (`FILINGS_DISCOVERY`, default false); with it off the worker has no HTTP client at all | `CommonSettings`, `worker._main` |
| Only two kinds of address are ever requested: the stock's own screener company page, and `https://www.bseindia.com/` filing PDFs matching one exact pattern. Every redirect target is checked before it is followed | `filings.is_official_pdf_url`, `polite_fetch` |
| An honest User-Agent naming the project and a contact; no browser disguise, cookies, or login. A refusal is accepted (retried later, never worked around) | `polite_fetch.USER_AGENT` |
| One page per stock per day; each filing fetched once (`documents.source_url` is unique) | `enqueue_discovery`, migration 0004 |
| Nothing of screener's own is stored or shown: not the page, not its numbers, not its pros and cons. Every fact is cited to the official BSE filing and its page | `filings.discover` keeps links only |
| The UI links to the filing's official address; the stored copy is never served (ADR 007 display policy) | `DocumentOut.source_url` |

the project notes's "no scrapers" rule is narrowed accordingly: *reading links from one allow-listed page*
under the rules above is permitted; scraping content, bypassing blocks, and any other site remain
forbidden.

## Consequences

- Documents appear without anyone uploading them, and stay current: new transcripts are picked up
  the day after they are filed.
- Citations point at the exchange filing, the most authoritative public source there is.
- **Residual risk, accepted by the owner:** screener.in's terms (as recorded in P0) allow personal
  viewing only. We read one page per stock per day and keep nothing of it but links to public
  exchange filings; the switch turns it off entirely. If screener objects or blocks us, we switch it
  off and fall back to uploads. BSE's terms page could not be read (JavaScript-only).
- screener's page layout can change; the parser then finds no links, discovery succeeds with nothing
  to do, and nothing breaks. Tests pin the structure with a synthetic page.
- Presentations are mostly hosted on company websites, which refused automated downloads in P0, so
  they are not fetched; annual reports cover the balance sheet instead.
- Migration 0004 was amended in place (source columns, two job kinds) rather than adding 0005: no
  lasting database held it (production is destroyed after every session, ADR 015).

## Amendment (P9d, 2026-09-23): three years of every BSE-hosted filing, and direct file addresses

- **Scope widened by the owner:** "the last 3 years, every PDF". `FILINGS_YEARS` (default 3, 1-5)
  now selects every earnings-call transcript and **investor presentation** dated within the window,
  the annual reports among the newest `FILINGS_YEARS` listed, and the **announcements** the page
  shows. About 85 PDFs for the three stocks (was 15). A call without a readable month is left out
  rather than guessed. Credit-rating reports (rating agencies' sites), presentations on company
  websites (about 26) and announcements older than the page's latest few remain out of reach.
- **Direct file addresses:** the first real run showed BSE's `AnnPdfOpen.aspx` script page often
  answering 406, while the file it redirects to (`xml-data/corpfiling/AttachHis/<same id>.pdf`)
  answered every time. Links are kept by that direct address; `AttachLive/` is tried for a filing made
  today. For such a filing BSE answers **503** (not 404) in `AttachHis/`, so 404 and 503 both mean
  "try the other folder"; 503 from every folder is an outage and is retried. A 2 s pause precedes
  every download. The three-year local run then fetched 71 of about 85 before the 503 fix.
- **Consequence for AWS:** the database is destroyed after every session (ADR 015), so each spin-up
  fetches and ingests the whole window again (several minutes for about 85 PDFs). Decided in the
  AWS step of P9d.

