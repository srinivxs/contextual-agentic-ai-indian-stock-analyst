# 024 — The scheduled RBI feed: a timer in the worker, dedupe in the database

- **Status:** Accepted (P15, 2026-09-29; the owner delegated the design).
- **Date:** 2026-09-29

## Context

ADR 007 chose RBI's press-release RSS as the one automated news feed, "switchable to fixtures"
because its terms are ambiguous. MVP item 12: the worker polls it on a schedule, polling again
creates no duplicates, and a switch replaces the live feed with fixtures.

## Decision

1. **`FEED_MODE=fixture` by default.** Nine synthetic items shipped in
   `app/feeds/fixtures/`, titled "Sample (fixture): ...", naming no real company, shown with a
   "Sample item" pill and no link. `FEED_MODE=live` reads
   `https://www.rbi.org.in/pressreleases_rss.xml` (checked on 2026-09-29: RSS 2.0, ten items, an
   ETag and Last-Modified).
2. **Polite and conditional.** The honest User-Agent, allow-listed addresses at every hop, a 2 MB
   cap, and `If-None-Match` / `If-Modified-Since` from the last answer, so an unchanged feed costs
   one small 304. No transaction is open during the request.
3. **Lenient, safe parsing.** ElementTree first; a body with a DOCTYPE or ENTITY declaration never
   reaches it (entity tricks) and goes to a small reader that uses plain string search, as does
   malformed XML. That reader reads each byte about once, so a hostile body costs time in
   proportion to its size (the security review found the first, regex-based version could be
   made quadratic). Markup is stripped; links off `rbi.org.in` are dropped, so the feed cannot
   point the app elsewhere. NUL characters are dropped, titles cut to 500 characters, links over
   1,000 skipped and at most 200 items read, so no single item can make every poll fail.
4. **Dedupe in the database.** `feed_items` is unique on (source, canonical URL) and on (source,
   title hash); inserts use `ON CONFLICT DO NOTHING`. Repeated or concurrent polls store each
   release once.
5. **A timer inside the worker is safe with several workers.** Every worker's timer queues
   `poll_feed` with a dedupe key naming the time slot; the jobs table's partial unique index plus
   `ON CONFLICT` keeps one job per slot, and `SKIP LOCKED` gives it to one worker. Two timers firing
   together make one poll, not two.
6. **Events only from items that name a company**, by code: a small alias table (HDFC Bank, Tata
   Consultancy Services / TCS, Reliance Industries) and a title-pattern table for the event type,
   sentiment and impact (a penalty is regulatory and negative, an appointment is neutral, and so
   on). No LLM: RBI titles are formulaic. Such an event cites the release (source `rbi`).
7. **Display (ADR 007's policy):** title, date, a short summary, the link, and "Source: Reserve
   Bank of India press releases (rbi.org.in)", on the Documents page's "RBI press releases" tab.

## Consequences

- Most RBI releases name none of the three companies, so the feed adds few stock events; that is
  honest (ADR 007: events are as plentiful as the real sources make them).
- Fixture mode keeps CI, tests and demos offline and free.

## Limits

- The feed's terms remain ambiguous (ADR 007); live mode is the owner's switch.
- Macro releases (the repo rate) are shown as news but not turned into stock events: their effect
  on a stock is a judgment the app does not make.
