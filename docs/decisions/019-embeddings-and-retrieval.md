# 019 — Meaning fingerprints (embeddings) by chunk text, exact search, a hard spending cap

- **Status:** Accepted (P10, 2026-09-27; the owner approved a $1 ceiling and the temporary local
  AWS pass).
- **Date:** 2026-09-27

## Context

P9 left about 16,500 chunks (paragraphs) from 77 BSE filings in the database. The chat (P12) must
find the handful that answer a question, by meaning, not by exact words: "attrition" should find
"employees leaving fell to 12%". ADR 010 already chose the model: Amazon Titan Text Embeddings V2 on
Bedrock in ap-south-1, 1,024 numbers per text, normalised.

## Decision

1. **One fingerprint per (model, text), not per chunk.** Table `embeddings(model, content_hash,
   embedding vector(1024), input_tokens)`, keyed by the chunk text's SHA-256. The same paragraph in
   two filings is paid for once; a document read again keeps its fingerprints; the model in the key
   means vectors of two models are never compared, and a model change simply shows as "missing".
2. **A timer, not a hand-off.** Every minute the worker queues `embed_document` for each ingested
   document that still has a text without a fingerprint for the current model. One query covers new
   filings, the first run, a model change, and jobs that stopped (cap reached, pass expired). A
   document is not queued again within 10 minutes of its last job.
3. **The job:** read the missing texts (one short transaction), call Bedrock 32 texts at a time, at
   most 4 calls at once, in a thread (`asyncio.to_thread`, boto3 is synchronous), with no
   transaction open; write with `INSERT ... ON CONFLICT (model, content_hash) DO NOTHING` after
   `still_mine`. Repeat runs and two workers at once leave one row per text.
4. **A hard spending cap.** `input_tokens` is what AWS billed per text, so `sum(input_tokens)` is
   the exact spend. Before each batch the job checks it against `EMBEDDING_TOKEN_BUDGET` (default
   10,000,000 tokens, about $0.20; the first full run needs about 3.5 million, about $0.07). At the
   cap it keeps what it made and fails with a message naming the setting. Overshoot: one batch at
   most.
5. **Off unless switched on.** `EMBEDDINGS_ENABLED=false` by default: without it the worker builds
   no Bedrock client at all, and an `embed_document` job fails with a clear reason. Tests and CI use
   a fake embedder and never call AWS.
6. **Credentials.** In AWS (GL), the ECS task role. Locally, a **temporary** pass copied from the
   owner's `aws login` session into the shell that starts Compose
   (`aws configure export-credentials --format powershell`), passed as `${AWS_...:-}` to the
   **worker only** (and to the api in P10b, for search queries). Never in `.env`, never in an image;
   it expires on its own. The pass comes from the least-privilege profile **`stock-analyst-cli`**
   (the one ADR 010's Bedrock checks used), **never** from `stock-analyst-admin`, the broad profile
   Terraform and the demo scripts use: a leaked pass should be able to do as little as possible.
7. **Exact search, no vector index.** At about 5,000 chunks per stock, comparing against every row
   takes milliseconds. An approximate index (HNSW) trades accuracy for speed at millions of rows;
   here it would add a concept to explain and buy nothing.
8. **Vectors travel as text.** A vector is sent as `'[0.1,0.2,...]'` and cast with
   `CAST(:v AS vector)` in SQL, so no pgvector Python package is needed.

## Consequences

- boto3 joins the runtime dependencies (typed behind the small `Embedder` interface; mypy ignores
  boto3's own missing types, as it does for pypdfium2). The backend image grows accordingly.
- A wiped local database (`docker compose down -v`) means paying for the fingerprints again (about
  $0.07). In AWS the database is destroyed after every session: GL must decide between
  re-embedding per `demo-up` (cents) and restoring from a snapshot.
- Titan V2 takes one text per call, so the first run makes about 16,000 calls: roughly 10 to 20
  minutes at 4 at a time.
