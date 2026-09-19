# 009 — Derived values are computed on read

- **Status:** Accepted
- **Date:** 2026-09-20

## Context

The application shows and reasons over a few derived values: a stock's **rolling sentiment**, its
**debt-to-equity ratio**, and simple **growth** figures. An earlier design precomputed and stored these
(sentiment snapshots, factor scores) and recomputed them under an advisory lock after each ingestion.
With three stocks and a few dozen facts and events, that machinery is more than the product needs, and
it adds cache invalidation, recompute jobs, and a race to reason about and defend.

## Decision

**Store only base records; compute derived values at query time** with small, pure, unit-tested
functions.

- **Base records** (stored): `extracted_facts` (metric, value, unit, period, basis, with document, page
  and verbatim quote) and `events` (sentiment, impact, event type, date, with citation).
- **Derived values** (computed on read, never stored):
  - **Rolling sentiment** for a stock: events within a recent window, each weighted by its impact
    (low/medium/high mapped to numbers) and by an exponential recency decay from the event date; the
    weighted mean of sentiment (negative/neutral/positive mapped to −1/0/+1). Returns the score, the
    event count, and a label. **With too few events it returns "insufficient data", not a number.**
    Window, half-life, impact weights, and minimum count are named constants in one module (values
    chosen at implementation and covered by tests). The function takes an explicit `as_of` time so
    it is deterministic in tests.
  - **Debt-to-equity** = total borrowings ÷ net worth, using facts from the **same period and the
    same basis** (standalone or consolidated). Different bases or a missing input → "not assessable".
    For **banks and other financial companies** it is "not applicable" (their borrowings are not
    comparable), decided by a per-stock classification.
  - **Growth** = change versus the comparable prior period (for example the same quarter last year)
    for the same metric and basis.
- Every derived value **returns the IDs of the facts and events it was computed from**, so an answer can
  cite it (a `D#` citation resolving to those inputs).

## Alternatives considered

| Option | Why not |
|---|---|
| Materialised tables recomputed after ingestion | Needs recompute jobs, an advisory lock, and cache-invalidation reasoning, for a handful of rows |
| Database views or SQL functions | Viable later; the logic is easier to unit-test and explain as plain Python |
| Ask the LLM to compute them | Violates "no LLM for deterministic work"; not reproducible |

## Reasoning

- **Consistent by construction.** The value always reflects the current facts and events. A correction
  or deletion takes effect immediately. Nothing can go stale.
- **Idempotency stays simple.** Ingestion writes only base records with unique constraints. There is
  no aggregate to protect against concurrent jobs, so no lock for derived state.
- **Easy to explain.** "It's a pure function of the stored facts" is a complete answer.
- **Cost is trivial** at this size: a few rows per request.

## Tradeoffs

- Cost grows with the number of facts and events per request. At larger scale one would materialise the
  values (for example a materialised view or a cached table). That change is local because callers use
  only the pure functions.
- Time decay means the same stock's sentiment changes as time passes even with no new data; that is
  intended, and `as_of` makes it testable.
- **Open question for the extraction phase:** what to do when two documents state different values for
  the same fact and period (for example a restated figure). The policy is decided in P11.

## Consequences for other decisions

- Supersedes the "precomputed factor scores at ingest" and "recompute under advisory lock" ideas in
  earlier notes; ADR 005 is amended accordingly.
- The scheduled worker no longer has a "recompute" job; it only polls the feed (ADR 008).
