# 020 — Facts from filings and screener.in, events, and values computed on read

- **Status:** Proposed (P11, 2026-09-27; the owner's decisions below). Accepted once the real run
  and the owner's hand check pass. **Amends [ADR 018](018-automatic-official-filings.md)** (screener's
  numbers are now used, not only its links) and the INR constraint in the project notes.
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

## Consequences

- The validator proves that the quote and the number are on the cited page, not that a value
  was read from the right column or year of a flattened table: the period and label checks, the
  disputed flag and the hand check reduce that risk; they do not remove it.
- screener's layout can change; the parser then finds nothing and the facts simply stop updating.
- Changing the prompt, the page selection or the vocabulary bumps `EXTRACTOR_VERSION` and runs the
  extraction again (about $0.70 each time).
