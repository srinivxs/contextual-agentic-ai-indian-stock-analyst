# 007 — Source strategy: user-supplied documents plus one public feed

- **Status:** Accepted
- **Date:** 2026-09-20

## Context

The challenge suggests scraping screener.in, ingesting publisher RSS feeds, and pulling prices from
yfinance. A feasibility spike (P0 and P0b, now closed) tested these against their robots.txt, their
terms, and real requests. The result: **the obvious sources are blocked or their terms forbid our use**
(storing, caching, or displaying content, and in one case explicitly retrieval-augmented generation).
The full list of what was checked and why it was rejected is in the table below.

The owner's direction: this is a portfolio project. Keep the scope small (three stocks, roughly
15–20 documents), keep every engineering concept real (ingestion, dedupe, idempotency, RAG,
citations), and **do not turn this into a data-licensing research project**. No scrapers, no
anti-bot workarounds, no paid data providers.

## Decision

Data enters through **two doors only**:

1. **User-supplied documents (primary).** The owner downloads real company documents by hand
   (quarterly results, results press releases, investor presentations) and uploads them through the
   application. Fundamentals and events come from these, extracted with page-level citations.
2. **One public feed (secondary): the Reserve Bank of India press-release RSS.** It exercises a real
   automated fetch, dedupe, and the scheduled refresh. A configuration switch (for example
   `FEED_MODE=live|fixture`) replaces it with a synthetic fixture feed if its terms ever become a
   problem.

Scope consequences:

- **Universe:** exactly three stocks: RELIANCE, TCS, HDFCBANK. Adding a stock later means adding a row
  and uploading documents; no code change.
- **Prices:** none. A `PriceProvider` interface may exist, but **nothing implements it in the MVP**.
  Consequently the system cannot assess *value* or *momentum* and must say so ("no price data")
  instead of guessing.
- **Events come from real documents.** They are derived by the pipeline from uploaded filings and
  press releases and from the RBI feed. **We do not hand-write event notes** to enlarge the dataset.
  Events will therefore be as plentiful as the real documents make them, and no more.
- **Source content is kept separate from our derived facts.** A document is stored as source content;
  a fact (for example revenue) is our own record with a citation to the document, page, and verbatim
  quote it came from.

### Provider interfaces (modular, replaceable)

`DocumentSource` (implemented: uploads), `FeedSource` (implemented: RBI RSS), `PriceProvider`
(declared only). New sources are added by implementing an interface plus a compliance record.

### Display policy

To respect the personal-use terms of the documents we hold:

- Original files are stored privately and **never served back to other users**.
- Answers and stock pages show facts and **short excerpts** (target: at most about 300 characters) with
  the document title, page, and a link to the original public URL.
- The deployment is private (Google OAuth test users only) and is destroyed after demonstration.

### Fixtures

- **Test fixtures are synthetic** and use a fictional company (`DemoCo`, ticker `DEMO`). They exercise
  edge cases: duplicates, malformed XML, conflicting figures, a scanned PDF with no text, a
  prompt-injection sentence.
- **Synthetic numbers are never associated with RELIANCE, TCS, or HDFCBANK**, in tests or anywhere else.
- The real demo data pack lives in git-ignored `data/local/`. The repo carries only a manifest of which
  official documents to download (title, publisher, URL, date), never their content.

## Source compliance record

Reviewed 2026-09-20. This is engineering diligence, **not legal advice**; terms can change.

| Source | What the terms say (summary) | We store | We display | Status |
|---|---|---|---|---|
| Uploaded company documents | Reliance's legal notice: "limited, personal, non-exclusive… license"; no copying to other servers. TCS IR blocks automated clients (owner downloads manually in a browser). HDFC Bank IR terms not located. | Private copy, extracted text chunks, facts with quotes | Facts and short excerpts plus link to the original | Accepted, residual risk noted |
| RBI press-release RSS | RBI disclaimer prohibits "caching and links to, and the framing of" the site except as set out; ambiguous for feed text. RBI publishes releases for public information. | Title, link, date, summary | Title, date, short summary, link, attribution to RBI | Accepted, **switchable to fixtures** |
| Synthetic fixtures | None | n/a | n/a | Test only |

## Sources evaluated and rejected

| Source | Reason |
|---|---|
| Publisher RSS: Economic Times | RSS is "personal use only"; forbids displaying, hosting, aggregating; forbids retaining copies |
| Business Standard | Terms explicitly forbid use "for grounding… or as part of retrieval-augmented generation", and caching |
| LiveMint | Forbids copying, public display, caching, archiving, AI/ML use |
| Moneycontrol | 403 Access Denied to automated clients; terms unreadable |
| Google News RSS | robots.txt `Disallow: /`; Google's terms forbid violating robots.txt |
| screener.in | Terms: personal viewing only; no copy, no public display, no mirroring |
| yfinance / Yahoo | robots.txt `Disallow: /`; Yahoo's terms bar automated collection; works only via browser TLS impersonation (its first request from an honest client got 429) |
| NSE bhavcopy / NSE site | NSE's data policy: end-of-day and historical data are NSE-owned, no redistribution; site returns 403 |
| Nifty constituents CSV | Copying needs written permission |
| Twelve Data / FMP | India coverage needs a costly tier / free plan appears US-only |
| GDELT | Terms are permissive, but the API returned 429 and bulk files 404 from the owner's network. Unverified. |
| Alpha Vantage | Personal non-commercial licence; needs a key and permission for a demo shown to others. Unverified. |
| BSE bhavcopy | Downloads work, but BSE's terms were unreadable. Unverified. |

**Revisit triggers (only if a concrete MVP need arises):** GDELT retested from AWS after deployment;
BSE bhavcopy for prices if the owner reads BSE's terms and wants a price feature.

## Alternatives considered

| Option | Why not |
|---|---|
| Use publisher RSS in a private demo anyway | Contradicts the terms we read; hard to defend in an interview |
| Hand-written event notes | Manual data invention just to enlarge the dataset; rejected by the owner |
| A paid data provider | The owner does not want to spend on data; not required for the MVP |
| Synthetic data for the three real tickers | Violates the "never invent financial data" constraint |

## Reasoning

A small amount of real, legitimately sourced data is enough to demonstrate every concept that
matters: a real ingestion pipeline, real document RAG with page citations, extraction with
verification, and honest abstention when data is missing. Being able to say "I read the terms and
designed around them" is a stronger interview answer than a scraper that violates them.

## Tradeoffs and residual risks

- Fewer events and thinner "news" than a live market feed would give.
- Fundamentals depend on documents the owner supplies; extraction quality depends on document layout
  (results press releases and presentations are far friendlier than 300-page annual reports).
- No price-based factors; the product openly says so.
- The RBI feed's terms are ambiguous; the fixture switch is the mitigation.
- Personal-use documents shown in a demo remain a grey area; the display policy above limits exposure.
