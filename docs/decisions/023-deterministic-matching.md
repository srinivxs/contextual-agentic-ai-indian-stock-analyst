# 023 — Matching: rules in code decide, the model only explains

- **Status:** Accepted (P14, 2026-09-29; the owner delegated the design: "its all urs").
- **Date:** 2026-09-29

## Context

The investor states preferences (ADR 022); "Match me" must say, per stock, *match / partial /
no match / not enough data*, each reason citing a stored figure (MVP item 8). An LLM scoring
stocks would be neither repeatable nor provable, and would be investment advice by another name.

## Decision

1. **Rules in code** (`app/matching/rules.py`, shapes in `app/matching/model.py`), one readable
   table with named thresholds:

   | criterion | asked by | rule | kind |
   |---|---|---|---|
   | debt to equity | avoid high debt | ≤ 1.0 | **hard** |
   | debt to equity | conservative | ≤ 0.5 | soft (with avoid high debt: one reason, > 1.0 fail, 0.5–1.0 miss) |
   | dividend | income style | latest dividend per share > 0 (paid or not, not a yield) | soft |
   | revenue (a bank: net interest income) growth | growth style, or aggressive | ≥ 10% | soft |
   | net profit growth | growth ≥ 10%; stability or conservative ≥ 0% (strictest wins) | | soft |
   | return on equity | quality style | ≥ 15% | soft |
   | price momentum | momentum style | six-month return of the adjusted close ≥ 0% (ADR 025); when prices do not reach back six months, **earnings momentum**: net profit growth this year above last year's, three years in a row from one source (`derived.growth_chain`) | soft |
   | value | value style | P/E ≤ `VALUE_PE_MAX` 20 (latest close over the latest full-year basic EPS); not assessable after a bonus or split since that year, with no prices or a loss; no EPS is no data | soft |
   | horizon | short term | one-year volatility ≤ `SHORT_TERM_VOL_MAX` 30% (steadier for a short holding); no data without a year of prices | soft |
   | horizon | long term | net profit did not fall over the three-year chain (each year ≥ the one before); no data without a chain | soft |
   | news sentiment | always | negative rolling sentiment is a **caution** only | — |

   Moderate and "debt is fine" add no rule. Debt to equity is not assessable for a
   bank (ADR 009).
2. **Hard filters versus soft scores.** A failed hard filter is *no match*. A hard filter with no
   data is *not enough data* (we cannot say a stock avoids high debt without its debt). Otherwise
   *match* needs every judgeable criterion checked and met; any miss, or a soft criterion with no
   data, is *partial*; nothing judgeable is *not enough data*. Cautions never change a status.
3. **Every reason carries its figure, its threshold and its citations** (the stored rows behind the
   derived value), money as reported.
4. **Two ways to see it.** The Match page (`GET /api/v1/match`) shows the verdicts with citation
   chips and uses no LLM at all. In the chat, a "Match me" question ("which one suits me?") with a
   stored profile adds the verdicts as `M#` evidence; the model writes the explanation, and the
   checker holds it to the same rules as any answer (it must show the verdict's figures, and the
   prompt forbids changing a status). Without a stored profile the question is answered from the
   figures as before.

**Amended 2026-09-29 (the owner's first look).** A profile of aggressive, debt is fine,
momentum and short term gave "not enough data" for every stock with nothing but "can't be judged"
lines. Momentum is now **earnings momentum** (profit growth speeding up, from stored figures of
one source, labelled as such: share-price momentum still needs prices); **aggressive** asks for
revenue growth of at least 10%; and the Match page shows preferences no stock can be judged on
(value, a horizon) once above the cards, and says plainly when nothing in the profile can be
checked, suggesting what to add.

**Amended 2026-09-29 (ADR 025, share prices).** With BSE's daily price files stored, three
criteria stop being "not assessable". **Momentum** is the six-month return of the split- and
bonus-adjusted close (at least 0% passes; without six months of prices it falls back to the
earnings momentum above). **Value** is P/E at or below 20; it is not assessable, with the reason,
after a bonus or split since the EPS year, because that per-share figure no longer compares with
today's price. A **horizon** becomes judgeable (short term: one-year volatility at most 30%; long
term: net profit did not fall over three years). Prices are cited to the day's BSE file. With no
prices at all the old behaviour stays (value not assessable, momentum from earnings). The
thresholds (20, 30%, 0%) are judgment calls like the rest, and not advice.

## Consequences

- The same profile and data always give the same verdicts; golden tests pin them for several
  profiles across synthetic stocks.
- The thresholds are judgment calls, stated in one place and easy to change; they are not advice,
  and the page says so.

## Limits

- Prices are end of day and only as deep as the background fetch has reached (ADR 025); with too
  little history the rules fall back or say "not in the data" rather than guess. No yield rule.
- About 1 in 10 extracted filing facts can be wrong (ADR 020); a verdict inherits that, and its
  reasons show the figures so a reader can check them.
