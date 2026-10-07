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

**BSE keeps about a month (2026-09-29).** 4 September downloaded; 3 September, 1 September and a
year back never did, even after long pauses: BSE serves only about the last month of daily files
at this address. So `PRICES_HISTORY_DAYS` is 30: the worker asks only for what exists, the few
days at the far edge are marked "no file" after three spaced refusals, and the **stored history
grows by one trading day per day** from 4 September 2026 (stored prices are kept). A full year
of history therefore takes a year; until then the chat panel's longer ranges stay greyed out,
momentum falls back to earnings momentum (six months of prices needed) and a short-term horizon
waits for about three months of prices (volatility needs 60 daily returns).

## Consequences

- The Match page can judge momentum, value and a horizon; the chat can answer price questions
  from stored, cited figures.
- A fresh database gets only the last month; the longer history exists only where the database
  has been kept. On AWS, where the stack's database is destroyed after each session (ADR 015),
  keeping price history needs a snapshot or keeping the database (a GL decision).

## Limits and risks

- BSE's terms page could not be read (JavaScript only); using the public daily file for personal
  research is a stated, owner-accepted risk, like ADR 018's.
- The rate limit's exact threshold is unknown; the pause is a setting and the stop rule keeps us
  within it.
- End-of-day only; a price can be a day old, and the page says so.

## Amendment (2026-09-30): "today" is India's date

The owner saw the 28 Sep close at 01:50 on 30 Sep. The rule "fetch every weekday up to
yesterday" was right, but "today" came from the server's clock, which in a container runs on
UTC: until 05:30 in India it still said 29 Sep, so "yesterday" was the 28th. Every "today" in
the app (the price days, the Update data button, sentiment, matching, search recency, the chat,
an undated RBI item) now comes from one function, `app/clock.py`'s `india_today()` (UTC+05:30,
India has no daylight saving), and a guard test fails if any other code reads the date from the
server's clock. The day's own file is still not asked for until the next day: BSE publishes it
after the close, and asking early would earn "slow down" strikes.

## Amendment (2026-10-07): declared holidays are skipped, and 8 files per run

On AWS every session starts with an empty database, and the month of prices took 1.5 to 2 hours:
each market holiday answers like "slow down", so it cost three 20-minute cool-downs before it was
believed (2 Oct and 14 Sep did exactly that). Two changes:

- **A holiday list** (`app/prices/holidays.py`): BSE's declared 2026 equity holidays are never
  asked for. Source: BSE's 2026 list as reproduced by brokers' pages (BSE's own page refuses
  automated reads; two copies agree on all 16 dates). It only skips days, so an omission falls
  back to the 406 rule and costs time, never data. The next year's list is added when published.
- **8 files per run** instead of 5, still 30 s apart: BSE refused only quick runs (seconds
  apart), and 8 files fit in 80 % of the job's 5-minute lease. A month now fills in about
  15 minutes instead of hours.
