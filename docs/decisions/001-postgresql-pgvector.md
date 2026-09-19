# 001 — PostgreSQL + pgvector as the single data store

- **Status:** Accepted
- **Date:** 2026-09-19

## Context

The system needs relational data (users, stocks, follows, fundamentals), vector
retrieval over a news corpus, vector storage for investor memory/persona, a job
queue for ingestion, and **transactionally safe, idempotent ingestion** (two jobs
for the same ticker must not double-index an article or corrupt state). The
challenge allows pgvector on RDS, Pinecone, or OpenSearch. This is a portfolio
project: thousands of chunks, not millions, and infrastructure is torn down
after each demonstration.

## Decision

One Amazon RDS **PostgreSQL 16** instance (single-AZ) with the **pgvector**
extension stores everything: relational tables, embeddings, memory, sessions and
the job queue (see ADR 005).

- The extension is enabled by the **first Alembic migration**, not Terraform
  (RDS lives in private subnets, so Terraform cannot reach it to run SQL).
- The embedding column dimension is fixed by the embedding model. It is **not
  chosen yet**; it is decided after Bedrock model availability is verified.
- Use an HNSW index with cosine distance, but measure against exact search
  filtered by ticker before trusting it. At our scale filtered exact search may
  already be fast enough.

## Alternatives considered

| Option | Why not |
|---|---|
| Pinecone | Separate vendor and secret; no joins with relational data; cannot share a transaction with article/tag writes. |
| OpenSearch | Managed clusters carry a meaningful always-on cost and operational surface. Conflicts with "near-zero cost when torn down". |
| DynamoDB + separate vector store | Two systems to keep consistent, and the data is relational. |

## Reasoning

1. **One transaction** covers article + chunks + embeddings + tags. This is the
   foundation of the idempotency and concurrency requirement.
2. **Hybrid queries are just SQL**: filter by stock, date window and
   `duplicate_of IS NULL`, then order by vector distance.
3. **One thing to run, back up, and destroy.**
4. Scale is tiny; a dedicated vector engine would be over-engineering.

## Tradeoffs

- RDS bills hourly while it exists (we destroy it after demos).
- Filtered approximate search can lose recall; mitigated by measuring and by the
  small per-ticker candidate sets.
- pgvector version is tied to the RDS engine version.
- Not a design for very large corpora; that is explicitly out of scope.
