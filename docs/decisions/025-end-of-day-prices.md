# 025 — End-of-day share prices from BSE's daily price files

- **Status:** Accepted (2026-09-29; the owner's decision, amending ADR 007's "no prices").
- **Date:** 2026-09-29

## Context

ADR 007 left prices out: every source checked was blocked, forbade our use, or cost about $329 a
month. The cost showed on the Match page: momentum, value and a time horizon could not be judged,
and the owner asked for share prices and their history. Of the options put to the owner (BSE's
daily price files, an Alpha Vantage free key, no prices), the owner chose BSE's files.

A probe on 2026-09-29 found the daily file ("bhavcopy", one CSV per trading day, every security:
open, high, low, close, the previous close, volume) at a fixed address per date, served to our
honest User-Agent. Requests a few seconds apart were answered with HTTP 406, and the same date
downloaded after a 30-second pause: a rate limit, which we respect.

## Decision

1. **Source:** BSE's daily equity price file, the same exchange whose filings we already fetch.
   Only the three stocks' rows are kept (matched by `stocks.bse_code`). End of day only: no live
   prices, and no indices (the equity file has none).
2. **Polite, a little at a time.** `PRICES_ENABLED` (off by default) lets the worker's timer queue
   `sync_prices` every few minutes (one job per time slot, as for the RBI feed). Each run fetches a
   few missing dates, newest first, with a long pause between files, and **stops at the first
   406/404**: BSE is asking us to slow down, and the next run continues. A date refused on three
   separate runs is recorded as having no file (a market holiday). Weekends are skipped. The first
   year of history takes about two hours in the background; after that, one file a day.
3. **Stored once:** `prices` is keyed by (stock, trading day); inserts use `ON CONFLICT DO
   NOTHING`, so repeated or concurrent runs store each day once. No transaction is open during a
   download.
4. **Bonus issues and splits, computed on read (ADR 009).** On an ex-date the file's previous
   close is already adjusted by BSE; our stored close for the day before is raw. Their ratio is the
   action's factor (0.5 for a 1:1 bonus); earlier closes are multiplied by the later factors, so a
   bonus never looks like a crash. Factors within 1% of 1 are rounding.
5. **What prices unlock (ADR 023 amended):** price momentum (six-month return; earnings momentum
   when prices do not reach back that far), value (P/E from the latest full-year EPS), a
   short-term horizon (one-year volatility), and on Home and the stock page the price history,
   the latest close and the day's change. P/E and yield are **not assessable** when a bonus or
   split happened after the EPS or dividend's year: those per-share figures no longer compare
   with today's price.
6. **Cited like a filing:** each price cites "BSE daily price file · <date>" and links to that
   day's file on bseindia.com.

**First real run (2026-09-29).** Six files 20 seconds apart downloaded; BSE refused the seventh.
A new time slot then started a run 0.2 seconds later, which asked for the same day again, and a
third run five minutes after that made three strikes: a normal Friday was recorded as a holiday
(the record was corrected by hand). Fix: after any refusal, **no run asks BSE for 20 minutes**
(`PRICES_COOLDOWN_MINUTES`), so strikes only count when far apart; and each run is gentler, 5
files 30 seconds apart. The year's first fill therefore takes about 8 to 12 hours (overnight
locally), then one file a day.

## Consequences

- The Match page can judge momentum, value and a horizon; the chat can answer price questions
  from stored, cited figures.
- Local and AWS runs need the backfill once per fresh database (about 8 to 12 hours at BSE's
  pace), or a restored snapshot (a GL decision).

## Limits and risks

- BSE's terms page could not be read (JavaScript only); using the public daily file for personal
  research is a stated, owner-accepted risk, like ADR 018's.
- The rate limit's exact threshold is unknown; the pause is a setting and the stop rule keeps us
  within it.
- End-of-day only; a price can be a day old, and the page says so.
