# 005 — PostgreSQL jobs table as the ingestion queue

- **Status:** Accepted
- **Date:** 2026-09-19

## Context

Ingestion (fetch, dedupe, chunk, embed, LLM-tag, recompute sentiment) is slow and
failure-prone, so it runs in a worker, not in the request path. Requirements:
durable jobs, retries, and safety when a scheduled refresh and a manual follow
hit the same ticker at once: **no duplicates, no corrupt state, idempotent.**

## Decision

Implement a queue as a **`ingestion_jobs` table in PostgreSQL**.

- **Statuses:** `pending`, `processing`, `completed`, `failed`.
- **Enqueue** is a single `INSERT` in the same transaction as the triggering
  action (e.g. the follow). A **partial unique index on `dedupe_key` where
  status in (`pending`, `processing`)** coalesces duplicate requests; a second
  enqueue returns the existing job.
- **Claim** with `SELECT ... FOR UPDATE SKIP LOCKED`, moving the job to
  `processing` with `locked_by`/`locked_at` (a lease). Expired leases are
  reclaimed.
- **Retry:** a failed attempt returns the job to `pending` with an
  exponential-backoff-with-jitter `run_after`; after `max_attempts` it becomes
  `failed` with `last_error`. Per-step warnings are recorded in `result` (JSONB);
  a job with warnings still completes.
- **Never hold a DB transaction across network I/O.** Fetch and call the LLM
  first; write in a short transaction with idempotent upserts
  (`ON CONFLICT`), plus an advisory lock around derived-state recomputation.
- **Decoupled interface:** worker code depends on a small `JobQueue` protocol
  (`enqueue`, `claim`, `heartbeat`, `complete`, `fail`). SQS could replace the
  implementation later. **SQS is not implemented now.**

## Alternatives considered

| Option | Why not |
|---|---|
| Amazon SQS | Enqueue and DB write are two separate writes (dual-write); needs an outbox or reconciliation anyway. Another service to provision. Explicitly excluded for now. |
| Redis/Celery, Kafka | Extra infrastructure with no requirement behind it. |
| FastAPI `BackgroundTasks` | Dies with the process; no retries or visibility. |

## Reasoning

- Transactional enqueue eliminates the dual-write problem.
- `SKIP LOCKED` is a standard, well-understood pattern for multiple workers.
- One less service to provision, pay for, and destroy.
- Correctness comes from unique constraints and idempotent writes, so even a
  duplicated or retried job is harmless.

## Tradeoffs

- Polling adds DB load and limits throughput; irrelevant at this scale.
- No built-in dead-letter or visibility-timeout tooling; we implement leases and
  `failed` status ourselves.
- Migration to SQS later needs an outbox or reconciliation; the `JobQueue`
  interface keeps that change local.

## Amendment (2026-09-20): MVP job types and scheduling

- **Job types** are now `ingest_document` (created when a file is uploaded) and `poll_feed` (the RBI
  feed, see ADR 007). The `refresh_stock` and `crawl_feed` types from the earlier design are dropped.
- **Scheduling** is a small timer inside the worker container that enqueues `poll_feed` once per time
  bucket; the partial unique index on the dedupe key makes duplicate ticks harmless (ADR 008). There is
  no EventBridge schedule.
- **Derived state** (rolling sentiment, ratios) is computed on read and never stored (ADR 009), so the
  advisory lock "around derived-state recomputation" is no longer needed. Ingestion writes only base
  records, protected by unique constraints and `ON CONFLICT`.
- The `JobQueue` interface, the four statuses, retries, leases and `SKIP LOCKED` claims are unchanged.

## Amendment (2026-10-07): lanes, so the first fill finishes within the hour

One worker ran one job at a time, so after a `demo-up` the AI reading waited behind slow, polite
downloads and the database took about an hour to fill. The worker now runs **lanes** inside one
process: one **web lane** for the jobs that reach BSE and screener.in (`discover_filings`,
`fetch_filing`, `sync_prices`; still one request at a time, so the politeness rules hold) and
`WORKER_AI_LANES` (default 2) **AI lanes** for `ingest_document`, `embed_document`,
`extract_document` and `poll_feed`. Each lane claims only its kinds (`claim_next(kinds=...)`),
SKIP LOCKED keeps two lanes off one job exactly as it does for two workers, and a test fails if a
kind of job belongs to no lane. Reading filings also makes 4 model calls at once instead of 2.
Trade-off: two AI jobs can now check the spending cap at the same moment, so it can be overshot by
at most one batch of calls per lane.
