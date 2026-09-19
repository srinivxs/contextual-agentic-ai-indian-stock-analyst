# 002 — FastAPI for the backend

- **Status:** Accepted
- **Date:** 2026-09-19

## Context

The challenge requires a Python backend (FastAPI or Flask). The backend serves a
REST API, runs OAuth login, executes a LangGraph agent whose time is dominated by
slow network I/O (LLM, embeddings, database), and hosts a separate worker
process for ingestion.

## Decision

Use **FastAPI** on Python 3.11 (matches the local machine, so dev and the
container use the same interpreter), served by Uvicorn, with **Pydantic** models
for requests, responses, settings, and LLM structured output.

- Database access: SQLAlchemy 2.0 (async) + asyncpg, with Alembic migrations.
- One codebase, **two entrypoints** (API and worker) built into one Docker image.

## Alternatives considered

| Option | Why not |
|---|---|
| Flask | No native async or request validation; both would be bolted on. |
| Django + DRF | Heavier than needed; its ORM/admin add little here. |
| Node/TypeScript backend | The AI stack (LangGraph, Bedrock SDKs, data tooling) is Python-first. |

## Reasoning

- `async` fits I/O-bound work (LLM calls, DB, HTTP fetches) and LangGraph's async API.
- Pydantic gives one validation mechanism for HTTP bodies, config, and model output.
- `Depends` gives clean dependency injection, so tests replace the LLM,
  embedder, clock, and DB session with fakes.
- Auto-generated OpenAPI documents the API contract for free.

## Tradeoffs

- Async requires discipline: sync SDKs (boto3, yfinance) must be wrapped with
  `asyncio.to_thread`, or they block the event loop.
- Async DB code is harder to debug than sync code.
- Python performance is irrelevant here; latency is dominated by the LLM.
