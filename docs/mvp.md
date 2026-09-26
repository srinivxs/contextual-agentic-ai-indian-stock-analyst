# MVP definition

> **Principle:** meet the challenge requirements, but minimise the data, infrastructure, and complexity
> required to demonstrate them. The interview value comes from *how the system works*, not from how
> much data it holds.

This file is the acceptance contract. The project is "done" when every criterion below is true.
Reasoning for the source and infrastructure choices is in ADRs 007, 008 and 009.

## What we are building

A working web application in which a user can log in, follow three stocks, see the company filings the app
ingests for them by itself, ask questions and get **grounded, cited answers in INR**, have their investment preferences
remembered, and see which of the three stocks match those preferences and why.

## In scope

- **Three stocks:** RELIANCE, TCS, HDFCBANK. The architecture allows adding more; we do not.
- **Data:** roughly 15–20 real documents and feed items in total (see "Data pack").
- **Real pipeline:** source → fetch → normalise → dedupe → chunk → embed → store → derive facts and
  events. Idempotent, safe against duplicate and concurrent jobs, and tested.
- **Real RAG:** page-level citations, numbers verified against the cited evidence, explicit abstention.
- **Simple investor memory:** `{risk_preference, debt_preference, investment_style, other_preferences}`.
- **Simple sentiment and events:** sentiment (positive/neutral/negative), impact (low/medium/high), and
  an event type per item; a rolling sentiment per stock, computed on read.
- **Deterministic personalised matching** over stored facts (debt, dividend, growth, quality) and the
  rolling sentiment. The LLM only writes the explanation.
- **One small LangGraph workflow.**
- **Scheduled refresh** of the one automated feed by the worker.
- **Full lifecycle:** Docker, Terraform, GitHub Actions, live on AWS, cleanly destroyable.

## Out of scope (deliberately)

More than three stocks; live or historical prices; value and momentum scoring; OCR; any scraping or
anti-bot workaround; any paid data provider; real-time data; multi-agent systems; teams, admin, or
settings screens; Kubernetes, Kafka, Redis, OpenSearch, DynamoDB, SQS, microservices, multi-region.
When a question needs something out of scope, the product says so plainly.

## Data pack

The dataset is intentionally small and **real**.

- **Official filings, fetched automatically (ADR 018):** for each of the three stocks, every
  BSE-hosted earnings-call transcript and investor presentation of the last three years, the last
  three annual reports and the recent announcements, downloaded from BSE by the worker once a day,
  when a user follows the stock, and when they press "Check for new filings" (at most once an hour
  per stock; only filings not already stored are downloaded; about 85 documents). Found through each stock's screener.in page, which is used for
  links only. There is no upload: the challenge asks the app to ingest data itself (P9d).
- **RBI press-release RSS**, ingested automatically (about ten current items at a time).
- **Events are derived by the pipeline** from those documents and that feed. Nobody writes event notes
  by hand to make the dataset look bigger. Events are as plentiful as the real documents make them.
- The documents live in git-ignored `data/local/`. The repository holds only a **manifest** of what to
  download (title, publisher, URL, date), never their content.
- **Test fixtures are synthetic** (fictional `DemoCo`, ticker `DEMO`) and are never associated with
  RELIANCE, TCS or HDFCBANK.

## Acceptance criteria

**Product**

1. A test user logs in with Google on the **live AWS URL**.
2. They follow RELIANCE, TCS and HDFCBANK and see basic information: name, NSE/BSE identifiers, sector.
3. Each stock page shows key facts **with citation chips**, a rolling-sentiment badge, and recent
   events derived from the ingested documents and feed.
4. On the Documents page they see each stock's filings, fetched automatically, when it was last
   checked, and a "Check for new filings" button (at most once an hour). A new filing's status
   moves pending → processing → completed, and its extracted facts and events then appear.
5. They say "I'm conservative, dividend-focused, and I avoid high debt." The profile updates and a
   "what I remember" panel shows it with the supporting quote. They can edit or forget any field.
6. They ask a factual question and get an answer where **every number and claim has a citation** that
   resolves to a real document, page, and short excerpt.
7. They ask something the data cannot support and get **"I don't have that in the data"**, not a guess.
8. "Match me" returns, per stock, *match / partial / no match / not enough data*, each reason citing a
   fact. It says "not assessable" where that is true (debt for a bank; value and momentum: no prices).
9. Money is shown in the currency the filing reports, labelled and never converted (₹ for almost
   everything; a figure the company states only in US$ is shown as reported, ADR 020).

**Engineering**

10. Ingesting the same document twice, or eight times concurrently, produces **one** document and one
    set of chunks and facts. An automated test proves it.
11. A failing ingestion job retries with backoff and ends in `failed` with the error recorded.
12. The worker polls the RBI feed on a schedule; polling again creates no duplicates. A configuration
    switch replaces the live feed with fixtures.
13. A prompt-injection sentence inside a document changes neither the user's profile nor the answer's
    behaviour.
14. Unit and integration tests pass with at least 80% coverage, plus one end-to-end flow.
15. `docker compose --profile app up --build` runs the whole stack locally (database, migration, api,
    web); the worker joins at P9.
16. `terraform apply` builds the AWS stack and `terraform destroy` removes it completely.
17. A push to `main` runs tests, builds and pushes the image, migrates the database, deploys, checks
    health, and updates the frontend, all automatically. A failing test blocks the deploy.

## Demo walkthrough (target)

1. Log in → follow the three stocks.
2. Open TCS: see cited facts, sentiment, events.
3. Open Documents → see three years of official filings, fetched and ingested automatically.
4. Tell the assistant your preferences → see them appear in the memory panel.
5. Ask a factual question → answer with citations; open one to see page and excerpt.
6. Ask for something not in the data → it abstains.
7. "Match me" → TCS vs Reliance vs HDFC Bank with cited reasons, and honest "not assessable" notes.
8. Show the pipeline, Terraform, and a passing CI/CD run.

## Known limits (stated up front)

- Events and news are limited to what the supplied documents and the RBI feed contain.
- Fundamentals depend on documents you supply, and extraction quality depends on their layout.
- The citation validator proves that IDs exist and numbers appear in the cited evidence. It does not
  prove full semantic entailment of every sentence.
- Not investment advice; the product says so.
