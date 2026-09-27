# 021 — The grounded chat: a fixed LangGraph workflow, numbered evidence, a code checker

- **Status:** Accepted (P12, 2026-09-27; the owner approved LangGraph, a $1 chat cap, US$ shown
  as reported, and the three stocks only).
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
   or cites facts or computed values with numbers but shows none of them (`figure_missing`, P12b).
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

## What it does not prove (the known limits)

- **Entailment.** The checker proves the numbers are in the cited evidence, not that the sentence
  means what the evidence says ("profit fell" citing a rise passes).
- **A cited passage vouches for itself.** A claim that cites a passage can repeat a number from that
  passage, including one a hostile document planted. The mitigation is the source itself (only
  official BSE filings are ingested) and that the answer shows the passage it cites.
- **Two cheap concurrent questions** can both pass the cap check; the overshoot is one question each.
- Nova 2 Lite's chat quality is judged on real questions at the end of P12; Nova Pro is the backup
  (ADR 010).
