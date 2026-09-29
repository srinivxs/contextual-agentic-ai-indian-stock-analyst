# 018 — Official filings found on screener.in and fetched from BSE, automatically

- **Status:** Accepted (P9c, 2026-09-23; the owner's decision). **Amends [ADR 007](007-source-strategy.md)**:
  company documents no longer depend on the owner downloading them. Uploads were a second door
  until the P9d amendment below removed them.
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
  off (there is no other door since uploads were removed). BSE's terms page could not be read (JavaScript-only).
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

## Amendment (P9d, 2026-09-23): uploads removed; this is the only door for company documents

- **Why:** the project brief never asks users to upload anything. It says the app should ingest a
  followed stock's fundamentals and news itself ("User follows an NSE/BSE ticker; the app fetches
  ..."). Uploads were our own workaround from P0, when no automatic source looked usable. With
  automatic filings working, the owner removed them.
- **What went:** `POST /api/v1/stocks/{symbol}/documents` (now 405), its size limit
  (`UPLOAD_MAX_BYTES`) and request-body reading; the api's file store and its Compose volume (the api
  only reads metadata; the worker alone writes and reads PDFs); the "Uploaded" group on the Documents
  page.
- **What stayed:** the dedupe guarantee (the same bytes recorded eight times at once give one
  document and one job), now tested at `record_document`, where it actually lives. The database keeps
  its `source` and `uploaded_by` columns and the `'upload'` value in `ck_documents_source`:
  migrations are forward-only, and dropping them would buy nothing.
- **Cost:** if screener.in blocks us or its layout changes, no new documents arrive until the parser
  is fixed. Already-ingested documents are unaffected.

## Amendment (2026-09-30): one "Update data" button for everything

The owner asked for one click that brings in every kind of data, and for the date the data is
updated to on every page. The per-stock **Check for new filings** button and its endpoint
(`/api/v1/stocks/{symbol}/filings/check`) are removed. In their place:

- **Update data**, in the top bar between the stock search and the light/dark switch:
  `POST /api/v1/data/refresh` queues, through the timers' own functions and within their limits,
  a filings check for every stock not checked within the hour (this rule is unchanged, and the
  same screener.in read refreshes the fundamentals table), a share-price run when a day is
  missing (ADR 025, BSE's cool-down still applies), and an RBI feed poll at most once per
  15-minute slot (ADR 024). A source whose switch is off is never queued, so the api now reads
  `PRICES_ENABLED` and `FEED_MODE` as well as `FILINGS_DISCOVERY` (Compose passes them; the AWS
  api task needs them at GL). New filings are then fingerprinted and read by the worker's timers.
- **The data note** under the top bar on every page (`GET /api/v1/data/status`): "Data updated
  to 29 Sep 2026: filings checked 29 Sep 2026, share prices to the 28 Sep 2026 close, RBI releases
  to 29 Sep 2026. Not live data.", with "Updating…" while any update job waits or runs.

A follow still checks that stock at once, within the same hour limit.

Later the same day the owner added: the button itself works **once an hour**, for everyone
together. Each press is recorded in `data_refreshes` (migration `0013`) under a transaction-scoped
advisory lock, so presses at the same moment count as one; a press within the hour gets 429, and
the status carries `next_update_at`, so the button is off until then and says when it works again.
When a run finishes, the stock page loads its cards again (the Fundamentals card included: the
filings check reads screener.in's page, which refreshes its top ratios).

## Amendment (P9d, 2026-09-23): a follow and a button check a stock at once

(Superseded in part by the amendment above: the button is now "Update data".)

- **Why:** the challenge names two triggers for ingestion, "a scheduled refresh and a manual
  follow" ("User follows a ticker; the app fetches ..."). The daily timer was the only one.
- **What:** following a stock (`PUT /api/v1/stocks/{symbol}/follow`) also queues a
  `discover_filings` job for that stock, in the same transaction as the follow row. The Documents
  page shows "Last checked 14:05 (12 minutes ago)" per stock and a **Check for new filings** button
  (`POST /api/v1/stocks/{symbol}/filings/check`; `GET` on the same path gives the status: enabled,
  checking, last checked, next check). A check reads the page and downloads **only filings not
  already stored**, so a repeat check costs one page read.
- **Rules:** at most one check per stock per hour (`CHECK_COOLDOWN_HOURS`, the owner's choice),
  for everyone together, to protect screener.in. Eight clicks at once give one job (the partial
  unique index on the live dedupe key). POST answers 202 while a check runs (whoever started it),
  429 within the hour, 409 when `FILINGS_DISCOVERY` is off; with the switch off a follow only
  follows. The api reads the same switch as the worker, so it never queues a job the worker cannot
  run. A manual check also counts for the daily timer, which then skips that stock for the day.
- **Considered and rejected:** a follow that expires after an hour, so that following again means
  "refresh". A follow is a lasting preference (the chat and the matching use it); an action to
  refresh is a separate thing, so it got its own button.
- **Still the api makes no outbound request:** it only inserts a job row; the worker fetches.
