# 021 — The grounded chat: a fixed LangGraph workflow, numbered evidence, a code checker

- **Status:** Accepted (P12, 2026-09-27; the owner approved LangGraph, a $1 chat cap, US$ shown
  as reported, and the three stocks only). Gate B passed locally on 2026-09-28; Nova 2 Lite kept
  for chat.
- **Date:** 2026-09-27

## Context

By P11 the database holds, per stock, cited facts (from filings and screener.in's table), derived
values computed on read, events, and about 16,500 searchable passages. The chat must answer a
question from these alone, cite every claim, and say "I don't have that in the data" rather than
guess. The model (Nova 2 Lite, ADR 010) is cheap but can invent, round or convert numbers, and the
passages it reads are untrusted text (a filing could contain instructions).

## Decision

1. **A fixed workflow, not a free agent (ADR 003).** `app/chat/graph.py`:
   analyze → update_memory (a stub until P13) → retrieve → grade → generate → validate → respond,
   with `abstain` and `out_of_scope` as the other two ends. Every edge is plain code. The model
   has no tools to call and cannot choose a path; it only writes the answer's sentences.
2. **Analyze and retrieve are code (rule 8).** `understand()` finds the stocks (from the question,
   else from the conversation), metrics, periods, and whether growth or events are asked. Retrieve
   loads the stocks' chosen facts and derived values (the same code as the stock page) and the
   closest passages (ADR 019), keeping only passages of the question's stocks with similarity ≥
   0.35. Search is optional: switched off or failing, the facts still answer.
3. **Numbered evidence.** Each item gets an ID: `F#` fact, `D#` derived value, `N#` passage,
   `E#` event (only for questions about news or events, with a `D#` rolling news sentiment per
   stock). Passages and events (whose summaries the extraction model wrote from filing text)
   travel inside `<document>` tags; the system prompt says text there is data, never an
   instruction. **Grade:** no evidence at all → abstain, with no LLM call.
4. **One structured call.** The model fills a forced tool form, `record_answer`: an outcome
   (`answer`, `not_in_data`, `out_of_scope`) and at most 8 claims, each with 1–5 evidence IDs.
5. **A deterministic checker** (`app/chat/answer_check.py`) refuses an answer when a claim has no
   citation, cites an ID that does not exist, contains a number that is not in the evidence it
   cites (so any computed, rounded or converted number fails), shows an amount in a currency its
   evidence does not use, contains a web address, is over 600 characters, cites more than 5 IDs,
   cites facts or computed values with numbers but shows none of them (`figure_missing`, P12b),
   or cites one measure for two periods from different sources (`mixed_sources`, P12c).
   A refused answer gets **one retry**, told only the problem codes; a second refusal abstains.
6. **Render is code.** Claims become text with `[n]` markers; each marker resolves to a stored row
   (filing and page with its official BSE link, screener.in section/row/column, or the derived value
   and its inputs). The model never writes a link.
7. **Money as reported (ADR 020).** US$ figures stay US$, labelled; nothing is converted.
8. **Scope.** Only RELIANCE, TCS and HDFCBANK. Anything else gets a fixed polite reply. No buy,
   sell or hold advice.
9. **Storage and API.** Migration `0008`: `conversations` and `messages` (the assistant's status,
   sources as JSON, model and tokens). `POST /api/v1/chat/messages`, `GET /chat/conversations` and
   `/chat/conversations/{id}`; a foreign conversation is 404. The engine runs with **no transaction
   open**; the question and answer are stored together afterwards in one short transaction, and
   nothing is stored if the engine fails.
10. **Off by default, with a cap.** `CHAT_ENABLED=false` means no engine and 409 "Chat is switched
    off". `CHAT_BUDGET_USD` (default 1.00) is checked before each question against the stored
    tokens × the model's prices; at the cap, 503. Overshoot is at most one question (about
    $0.003).

## Consequences

- Every number shown was in a stored, cited row; an answer that cannot prove that is not shown.
- Tests cover a grounded answer, an abstention without an LLM call, a retry told what failed, an
  invented number ending in abstention, out of scope, a prompt-injection passage, a failed search,
  and a follow-up taking its stock from the conversation. CI uses a fake LLM and embedder.

## The first real answers (P12b, 2026-09-27)

The owner's first two real questions cost about $0.003 together and both passed the checker,
yet showed four faults, all fixed in P12b:

- A comparison ("TCS shows stable growth with low leverage") wrote **no numbers at all**, so the
  number check had nothing to test; one claim compared a bank's leverage with a company's, which
  ADR 009 rules out. Fix: `figure_missing`, and prompt rules (a judgment shows its figures; debt
  to equity does not apply to banks; compare stock by stock, no winner the figures do not show).
- Claims cited up to ten IDs although the form allows five: the model does not always honour the
  schema. Fix: `too_many_citations` in the checker.
- "Recent news" reached the model with **no events**: understanding saw the word, retrieval never
  loaded them. Fix: `E#` items and the news sentiment.
- TCS's FY2025 net profit came back twice, ₹48,797 crore (screener.in, the whole group) and
  ₹48,553 crore (an annual-report passage, the shareholders' share), with no word on why. Both
  are true. Fix: a prompt rule (prefer the fact; if both, say they measure different things).
  Code cannot check this one.

The second run (same questions plus two more) was grounded throughout: every judgment showed its
figures, HDFC Bank's leverage was not compared, the news and its sentiment were used, and TCS's
FY2025 profit came back once. One subtle fault remained, fixed in P12c: Reliance's revenue "rise"
paired the annual report's FY2025 figure (₹9,80,136 crore) with screener.in's FY2026 (₹10,55,780
crore), although screener.in's own FY2025 is ₹9,62,820 crore: the two count revenue differently.
Fix: each fact line names its source, and a claim citing one measure for two periods from
different sources is refused (`mixed_sources`); the growth value compares like with like.

## Amendment: the owner's review of real answers (2026-09-29)

Six real questions went wrong. The fixes keep the workflow's shape and add code, not model
judgment:

| Seen | Cause | Fix |
|---|---|---|
| "What was Reliance's revenue in FY2024?" abstained | the model chose "not in data" although the figure was an F item | a question that only asks for stored figures (`understand.py`: "what is/was ..." or "how much", a metric of ours, no growth, reason, news, price, match or judgment word, a stock named) is answered by code: `lookup.py` states each figure with its period, basis and source; no LLM call |
| The annual report's figures in the text, screener.in's in the table | the table is one source; the facts were the best figure per year | one source per measure per answer (`insights.measure_facts`): the best-ranked source that has every year the question needs; changes (`change_views`) come from those same figures; every F and D item lists the figures it rests on; `conflicting_figures` refuses an answer resting on two figures for one stock, measure, period and basis; the table is shown only when it agrees (`tables.fits_answer`) |
| A differing figure chosen silently | the hierarchy was applied without a word | another source's differing figure is its own F item (`rivals` / `rival_of`); code adds one sentence naming it with its source when the answer does not (`render.disclosures`) |
| "Why did revenue change?" repeated the figures | no rule for causes | `cause_without_source`: "because", "due to", "driven by" ... must cite a passage, event or match verdict; a why-answer citing no passage or event ends with "The available data shows the change, but I don't have sufficient source material to establish why it occurred." |
| "What will TCS's share price be next year?" called out of scope | the model's out-of-scope was taken as is | a future share price of a named stock is refused by code ("I don't have a verified future share-price prediction ..."); the model's out-of-scope stands only when no stock of ours is named, otherwise it is "I don't have that in the data"; the reply names the other company only if the question writes it word for word |
| A net profit table under a question about an earnings call | "earnings" matched the profit metric | "earnings call/presentation/release ..." name a document; a table also needs the answer to cite its measure |

Also read by code: "last/past N years", "latest", "standalone"/"consolidated", and "March 2024",
"fiscal 2024", "financial year 2024" as FY2024. Computed values now say which stored figures they
used ("Figures used: ₹9,62,820 crore (FY2025), ₹10,55,780 crore (FY2026).").

Kept on purpose: "I don't have that in the data." stays the wording for a stock of ours whose
data lacks the answer. Trade-off: preferring one source per answer means a
trend may use screener.in's figure for a year where the annual report ranks first; the annual
report's figure is then disclosed beside it, never dropped silently.

## Second amendment: the owner's pressure test (2026-09-29, evening)

| Seen | Cause | Fix |
|---|---|---|
| "What is Infosys's FY2025 revenue?" answered with TCS's revenue | a question naming none of our stocks took TCS from the previous turn, and the lookup stated it | `app/chat/entities.py` finds other companies **before retrieval**: well-known names in any case, and capitalised names where a company stands (possessive + financial word, name + financial word, "net profit of X", comparison lists); longer names containing ours ("HDFC Life", "Reliance Power") are others. Such a question never takes stocks from the conversation and is refused with no retrieval and no LLM call. And a question naming none of our stocks borrows one from the conversation **only when it refers back** ("its", "their", "the company", "and ...", "what about ..."): so an unknown company no list holds ("what is zomato revenue") reaches the model with all three stocks, and the model's "out of scope" stands. A name right after one of ours ("Reliance's Consolidated", "Jio's ARPU") is never taken for another company: refusing our own question is worse than the leak |
| No route by kind of question | flags only | `understand.py` assigns one **intent** (unsupported company, memory read, future, source request, personalised, valuation, explanation, source conflict, lookup, calculation, comparison, trend, news, price, general); the graph routes on it |
| "What do you remember about my preferences?" → "not in the data" | nothing read memory back; the model may cite only evidence | a `recall` node reads the profile back by code (ADR 022 amendment) |
| "Which ... should I research further?" → "not in the data" | not read as a personal question | "should I research/consider/buy", "my (stated) preferences" route to matching; with no profile saved, code says how to give one |
| The two Reliance revenue figures given without a reason | a figure carried no definition | each figure carries what its source calls it: screener.in's row ("Sales") or the filing's own line, read from the stored quote ("Revenue from Operations"); disclosures name both and say the data does not establish they measure the same thing |
| "Why did revenue change?" answered with "resilient performance" | any cited passage counted as an explanation | a why answer counts as explained only when a claim gives a cause (because, due to, driven by ...) that a cited passage or event states; the search for a why question adds "what drove the change"; otherwise code adds "The available data confirms the change, but I don't have sufficient source material to establish the specific reasons for it." |
| "Is TCS undervalued?" and "what would you need?" → "not in the data" | no valuation route | a `valuation` node gives the stored price-to-earnings with its inputs, then what a verdict needs that the data does not hold (other companies' or long-run multiples, forecasts) |
| "Positive sentiment supports the performance" | no rule | `sentiment_as_evidence`: a claim citing the news sentiment may not say it supports, confirms or is consistent with anything |
| "Where exactly did you get that?" re-asked the model | history held text only | the history carries each answer's sources; a `sources` node lists the previous answer's own sources |
| "Reliance's AI revenue" answered with total revenue | the lookup ignored the qualifier | a metric word with a qualifier ("AI revenue", "operating profit") is never a lookup of the total |

Also: "I want to know whether ..." is never stored as a preference (ADR 022); computed changes
show their calculation and difference; a question about disagreeing sources gets every
disagreement disclosed by code, or "The stored sources agree (within 1%)".

## Third amendment: substitution, asking back, follow-ups (2026-09-29, night)

| Seen | Cause | Fix |
|---|---|---|
| HDFC Bank's "revenue" answered with its net interest income | "revenue" was read as both top lines, a bank's being net interest income | "revenue" means revenue from operations only; a figure a stock lacks is never replaced: code adds "HDFC Bank's revenue from operations: not available in the current data." (`code_answers.missing_lines`), and a lookup of it alone is answered that way, with no LLM call. Net interest income is given when asked for by name |
| "What is the latest revenue?" answered for two companies | no company named meant all three | a figure of no named company, not about all three ("which", "compare", "all", "rank" ...), is asked back: "Which company do you mean: TCS, HDFC Bank, or Reliance?", with a **choice** per company; a click sends the full question ("What is the latest revenue for Reliance?") at once. A comparison with no measure is asked back the same way: revenue, net profit or both |
| "Now compare it with HDFC Bank." lost Reliance | a question naming a stock never looked back | a comparison pointing back ("compare it with", "how does it compare to") keeps the earlier stock beside the named one; any follow-up with no measure, period or window of its own keeps the earlier turn's |

Choices travel with the live reply only (`Reply.choices`, the API's `choices`): they are not
stored, so a reopened conversation shows the question asked back without buttons. No new
status or table was needed: a question asked back is an answer with choices and no sources.

## What it does not prove (the known limits)

- **Entailment.** The checker proves the numbers are in the cited evidence, not that the sentence
  means what the evidence says ("profit fell" citing a rise passes).
- **A cited passage vouches for itself.** A claim that cites a passage can repeat a number from that
  passage, including one a hostile document planted. The mitigation is the source itself (only
  official BSE filings are ingested) and that the answer shows the passage it cites.
- **Two cheap concurrent questions** can both pass the cap check; the overshoot is one question each.
- **Company detection is by list and position**, not understanding: an unknown company written in
  lower case and not on the list is not caught by code; the model's "out of scope" is then the
  fallback, and it cannot borrow our stock from the conversation unless the question refers
  back. A proper name in a company position that is not a company may still be refused.
- **A cause that a passage states is not proven to be the cause**: the check proves a "because"
  cites a document, not that the document says exactly that (no entailment).
- Nova 2 Lite's chat quality is judged on real questions at the end of P12; Nova Pro is the backup
  (ADR 010).
