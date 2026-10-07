# 020 — Facts from filings and screener.in, events, and values computed on read

- **Status:** Accepted (P11, 2026-09-27), after the real run and the hand check below. **Amends [ADR 018](018-automatic-official-filings.md)** (screener's
  numbers are now used, not only its links) and the project's INR-only constraint.
- **Date:** 2026-09-27

## Context

P10 made the filings searchable. The challenge wants more: cited fundamentals ("which news article
/ fundamentals row"), per-stock sentiment updated as documents arrive, and personalised picks on
growth, value, stability, momentum and quality. That needs **numbers we can prove** and **events**,
not just passages.

Two facts about the real data shaped this (found by reading the stored pages):

- pypdfium2 extracts the rupee sign in annual reports as the letter **"H"** ("H74,671.30 crore").
- Some tables **mix "₹ crore" and "US$ million" columns**; TCS states some figures only in US$.

## Decision

1. **A fixed vocabulary of 10 metrics** (`app/vocabulary.py`): revenue from operations, net
   interest income (banks), net profit, total borrowings (non-banks), total equity, dividend per
   share, basic EPS, return on equity, net interest margin and gross NPA ratio (banks).
2. **Two sources of facts, one `facts` table**, each row carrying its citation:
   - **Filings, read by an LLM** (Nova 2 Lite, ADR 010): a deterministic page selector keeps about
     480 of 5,600 pages; the model fills a fixed form (a forced tool call) with the metric, the
     value as printed, unit, currency, period, basis, page and a **verbatim quote**; a
     **deterministic validator** (`app/fact_validation.py`) accepts a fact only if the quote is on
     the page, the label and the number are in the quote, the currency, scale and period are
     evidenced, and the value is in range. Rejections are recorded as codes, never with text.
   - **screener.in's fundamentals table, parsed by code** (`app/screener_numbers.py`), from the
     company page the worker already reads once a day: the cited "fundamentals row" is the
     section, row and column plus the page URL. No LLM. **The owner's decision, overriding the
     rule that screener is used for links only**; screener's terms (personal viewing only) are an
     owner-accepted risk, kept small: the same one page per stock per day, an honest User-Agent,
     nothing behind a login, and robots-disallowed paths never read.
3. **Money is stored and shown in the currency the filing reports, never converted** (the owner's
   decision): INR amounts in crore, US$ amounts in millions, per-share values in the reported
   currency. There is no FX source, so a conversion would be invented data. Derived values and
   comparisons only ever combine figures of **one** currency. The UI and the chat label a US$
   figure as reported in US$. (The challenge text says "all figures in INR": a US$ figure appears
   only verbatim, cited and labelled, which is recorded here as a deliberate choice.)
4. **Conflicts are resolved on read** (`choose_fact`): annual report, then screener.in, then
   investor presentation, then earnings call, then announcement; within one rank the latest source
   wins. Consolidated and standalone never compete. A candidate more than 1% away from the winner
   makes the fact **disputed**, and both sources are shown. Every row is kept.
5. **Events** (a fixed list of 11 types) with sentiment and impact, each with its page and quote,
   from announcements and earnings calls. **Rolling sentiment** is computed on read: a 365-day
   window, a 90-day half-life, impact weights 1/2/3; fewer than 3 events is "insufficient data".
6. **Derived values on read** (ADR 009): debt to equity (not applicable for banks; not assessable
   when inputs are missing, on different bases or in different currencies), year-on-year growth,
   latest dividend.
7. **Cost control, as in P10:** `EXTRACTION_ENABLED` off by default; every LLM call is recorded
   with its tokens; the job stops at `EXTRACTION_BUDGET_USD` (the owner approved $2; the first
   full run is expected to cost about $0.70) and keeps what it made.
8. **Model check:** after the first run the owner hand-checks 20 facts against the PDFs; more than
   2 wrong values, or an acceptance rate under 60%, means switching to Nova Pro (about $1.10).

## Outcome of the first run and the hand check

- **Trial, then the real run.** A one-cent trial (6 calls, 68% accepted) came first. The real run
  read all 84 filings with Nova 2 Lite: 225 calls, **$0.49** (cap $2, expected about $0.65).
  Stored after the re-checks: 93 filing facts, 272 screener.in figures, 88 events.
- **Acceptance.** Events 115 accepted, 68 refused. Facts 156 accepted, 325 refused, mostly
  `label_not_in_quote`: the model quoted a row of numbers whose label sits on another line of a
  flattened table. That is strict proof refusing true figures, not invented numbers.
- **Pitfalls on real data.** The rupee sign extracts as "H", "C" or a backtick; tables mix ₹ crore
  and US$ million columns; Reliance's headline "revenue" (Value of Sales and Services, gross of
  taxes) is not Revenue from Operations. Fixed by accepting the statutory label only; the free
  re-check removed 17 stored facts.
- **Hand check** (20 filing facts, about 7 per company, against the stored page text and
  screener.in): 14 right, 6 wrong. By the owner's test (right number, right year) 3 wrong: a gross
  NPA of 1.9% that belonged to HDB Financial (a subsidiary), not the bank; a return on equity read
  from the wrong column of a flattened table; a dividend paid during FY2026 (for FY2025) stored as
  FY2026. The other three were definition traps: non-current borrowings stored as total
  borrowings; a final dividend (₹31) instead of the year's total (₹110); net profit "excluding
  exceptional items".
- **The owner's choice (option A).** The rule in decision 8 (more than 2 wrong) pointed to Nova
  Pro. The owner chose free, deterministic **definition guards** in the validator instead: a net
  profit quote that says excluding or before exceptional items, adjusted, underlying or normalised
  is refused; a dividend must be the year's total or a per-share table figure, not "final",
  "interim" or "special" alone, and not a dividend paid or appropriated during the year; total
  borrowings needs "total borrowings", "total debt" or "gross debt", not a lone "Borrowings" line.
  The guards were applied to the stored facts by the free re-check (`python -m
  app.recheck_facts`). Nova Pro was not adopted.
- **Known limitation.** Reasoning errors (wrong entity, wrong column) remain at roughly 1 in 10
  accepted filing facts. They are mitigated, not removed, by the "disputed" marker when another
  source disagrees and by ranking annual reports and screener.in first.

## Consequences

- The validator proves that the quote and the number are on the cited page, not that a value
  was read from the right column or year of a flattened table: the period and label checks, the
  disputed flag and the hand check reduce that risk; they do not remove it.
- A new definition guard costs nothing to apply: the re-check runs the validator over stored facts
  without calling the LLM.
- screener's layout can change; the parser then finds nothing and the facts simply stop updating.
- Changing the prompt, the page selection or the vocabulary bumps `EXTRACTOR_VERSION` and runs the
  extraction again (about $0.70 each time).

## Amendment (2026-09-30): the stock page shows one figure

The owner asked for a cleaner stock page: where sources disagree, it now shows only the figure
chosen by the conflict policy above (the best-ranked source), without the "disputed" tag or the
list of sources that disagree, and every source there is a small link icon (its name and hover
give the full source). Nothing changed in how figures are stored or chosen, and the chat still
names another source's differing figure beside the one it uses (ADR 021 amendments).

## Amendment (2026-09-30): the Fundamentals card

The owner asked for a Fundamentals card on the stock page, for all three stocks, in place of the
share price card's returns, volatility, P/E and dividend yield: Mkt Cap, ROE, P/E Ratio (TTM),
EPS (TTM), P/B Ratio, Div Yield, Industry P/E, Book Value, Debt to Equity, Face Value.

- **Source:** the "top ratios" list of the same screener.in company page the worker already reads
  once a day (no new page, no new request; rule 9 and ADR 018 still hold). Parsed by code
  (`screener_numbers.parse_top_ratios`, written in P11 and unused until now) and stored one row per
  stock in `screener_ratios` (migration `0013`), replaced on each read, so "Update data" refreshes
  it. A missing item is NULL, never zero.
- **Computed on read (ADR 009):** EPS (TTM) = screener.in's current price / its P/E, and P/B =
  its current price / its book value per share (the same moment's figures); Debt to Equity is ours
  (stored borrowings / equity), "not applicable" for a bank.
- **Not available, said plainly:** Industry P/E. The company page read does not show it and no
  other page is read.
- **Honest limits:** screener.in's figures are as of its read (the card says when), not BSE's
  end-of-day close in the share price card; the P/E is screener.in's own trailing-twelve-months
  figure. The share price card now holds only the close, the chart and corporate actions (the
  price API still sends returns, volatility, P/E and yield: matching and the chat use them).

