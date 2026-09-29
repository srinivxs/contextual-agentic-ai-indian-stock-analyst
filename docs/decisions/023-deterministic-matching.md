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
   | dividend | income style | latest dividend per share > 0 (no yield: no prices) | soft |
   | revenue (a bank: net interest income) growth | growth style, or aggressive | ≥ 10% | soft |
   | net profit growth | growth ≥ 10%; stability or conservative ≥ 0% (strictest wins) | | soft |
   | return on equity | quality style | ≥ 15% | soft |
   | earnings momentum | momentum style | net profit growth this year above last year's, three years in a row from one source (`derived.growth_chain`) | soft |
   | value | style | not assessable: needs share prices | — |
   | horizon | long or short term | not assessable: no price history | — |
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

## Consequences

- The same profile and data always give the same verdicts; golden tests pin them for several
  profiles across synthetic stocks.
- The thresholds are judgment calls, stated in one place and easy to change; they are not advice,
  and the page says so.

## Limits

- No prices: no yield, value or momentum; the app says so rather than guessing.
- About 1 in 10 extracted filing facts can be wrong (ADR 020); a verdict inherits that, and its
  reasons show the figures so a reader can check them.
