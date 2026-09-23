# Roadmap

Re-cut around the MVP (see [mvp.md](mvp.md)). Decisions behind it: ADRs 007, 008, 009.

## How we work

- **Small phases.** Each phase is one branch, one reviewed change, small enough to read end to end.
- **Explain first, build second.** Before code: what we are building, why, and the files that will
  change. After code: how the data flows and how to explain it.
- **You must be able to explain every phase in an interview.** Each phase ends with a short "explain
  it back" list. If something is too clever to explain, we simplify it.
- **The schema grows with the feature that needs it.** No big-bang schema. Each phase adds only its own
  tables through its own Alembic migration, designed and explained at the start of that phase.
- **Vertical slices.** A phase that adds behaviour includes the smallest UI needed to use it, so the
  product is always demonstrable.
- **Tests first, deploy early.** TDD throughout. From Gate A onward, everything ships through the
  pipeline.
- **Plain code.** Few dependencies, small functions, explicit over clever, no abstraction without a
  second user.

## Overview

| Phase | Name | Status |
|---|---|---|
| M0 | Repository governance | ✅ done |
| M1 | Backend skeleton | ✅ done |
| D1 | Docs re-cut around the MVP | ✅ this commit |
| P2 | Bedrock verification and model decision | ✅ done (ADR 010) |
| P3 | Database foundation | ✅ done (ADR 011): compose DB, two roles, engine, `readyz`, Alembic, `stocks` |
| P4 | Authentication and sessions | ✅ done (ADR 012): P4a sessions, `/me`, logout; P4b Google login (PKCE, ID-token verification); P4c docs and real-Google check |
| P5 | Follow API and frontend shell | ✅ done (ADR 013); manual local flow verified by the owner |
| P6 | Docker | ✅ done (ADR 014): P6a backend image and `migrate`; P6b frontend image and the Compose `app` profile; P6c docs and the manual check |
| P7 | Terraform (cost table first) | ✅ done (ADR 015): P7a bootstrap + state bucket; P7b network; P7c database, secrets, registry, IAM; P7d load balancer and ECS; P7e1 the persistent edge; P7e2 production mode and the live drill |
| P8 | CI/CD → **Gate A: login and follow live on AWS** | ✅ done (ADRs 016, 017): P8a registry + GitHub OIDC; P8b checks + image; P8c deploy job; demo scripts; **Gate A passed 2026-09-23** |
| P9 | Document ingestion core | |
| P10 | Embeddings and retrieval | |
| P11 | Fact and event extraction, derived values | |
| P12 | Grounded chat (LangGraph) → **Gate B: cited RAG chat live** | |
| P13 | Investor memory | |
| P14 | Deterministic matching → **Gate C: full MVP working** | |
| P15 | Scheduled RBI feed | |
| P16 | Hardening, docs, demo rehearsal | |

The gates matter: at each one there is something complete and demonstrable, so stopping early still
leaves a real product.

---

## P2 — Bedrock verification and model decision

- **Goal:** know which Bedrock models we can actually use, and pick a chat model and an embedding model
  before any vector column exists.
- **Build:** no application code. You configure a local AWS SSO profile (no keys, no resources
  created); we list available models in the candidate regions, test access, compare cost, choose the
  AWS region, and record the decision in an ADR. The embedding dimension is fixed here.
- **Explain first:** why the embedding dimension fixes the database column; why IAM auth beats API keys.
- **Done when:** an ADR names the chat model, embedding model, dimension, and region, with the checks
  behind them.
- **You should be able to answer:** why not just pick the newest model? What does the embedding
  dimension affect?

## P3 — Database foundation

- **Goal:** a real PostgreSQL + pgvector database with migrations, and a truthful readiness probe.
- **Build:** Docker Compose Postgres with pgvector; async engine and connection pool; Alembic; the
  first tables (the three seeded `stocks`); `/api/readyz`; integration tests against a real container.
- **Explain first:** connection pooling and its arithmetic; transactions; what a migration is; why the
  pgvector extension is enabled by a migration.
- **Done when:** migrate up, down, up passes; `readyz` reports database health; tests run against a
  real database.
- **You should be able to answer:** what is a connection pool? Why liveness and readiness separately?

## P4 — Authentication and sessions

- **Goal:** Google login with our own server-side session.
- **Build:** OAuth authorisation-code flow with PKCE, `state` and `nonce`; `users` and `sessions`;
  `GET /me`; logout; CSRF protection.
- **Explain first:** the OAuth/OIDC flow step by step; why we hash session tokens; cookie flags.
- **Done when:** tests (with a mocked Google) cover a tampered `state`, an expired session and logout.
  Met, and checked once against the real Google. The tests: a tampered or mismatched `state`
  (`tests/api/test_auth_callback.py`, `tests/unit/test_login_state.py`), an expired session
  (`tests/integration/test_auth_sessions_db.py::test_an_expired_session_finds_nobody`) and logout
  (`tests/integration/test_auth_api_db.py`, `tests/api/test_auth_no_db.py`). ADR 012 section 6 maps
  every security guarantee to its test.
- **Built in three steps:** P4a (`users`, `sessions`, `/me`, logout, Origin check), P4b (PKCE and signed
  login state; JWKS cache and ID-token verification; the login and callback routes) and P4c (ADR 012
  and the real-Google end-to-end check).
- **You should be able to answer:** what does `state` protect against? Why server-side sessions?
  Why store only a hash of the session token? Why does the login cookie not use `__Host-`? What stops
  a forged callback from ever reaching Google? Why is the database opened only after the ID token is
  verified?
- **Left for later (in ADR 012):** rate limiting, CSP, the JWKS stale-refresh cooldown (P16); the
  second Google client and SSM secrets (P8); CloudFront cookie forwarding (P7).

## P5 — Follow API and frontend shell

- **Goal:** the first full-stack slice, working locally.
- **Build:** `user_follows`; follow and unfollow endpoints (idempotent `PUT` and `DELETE`); Next.js
  static-export app with sign-in and a Stocks page (three cards, follow toggle).
- **Explain first:** idempotent verbs; static export and its limits (ADR 006); same-origin cookies.
- **Done when:** you log in locally, follow a stock, refresh, and it persists. Automated: one backend
  flow test (`tests/integration/test_login_follow_flow_db.py`) and the frontend tests. The manual run
  (`npm run dev` on port 3000 with the backend on 8000) was done by the owner on 2026-09-21.
- **You should be able to answer:** why is follow a `PUT`? What does static export forbid? Why does
  the check order go Origin, session, validation? How do concurrent follows converge on one row? Why
  does local development need a proxy, and why is that not CORS? Why is a 500 from `/me` not a sign-out?
- **Left for later (in ADR 013):** stock detail; the frontend Dockerfile and true single-origin parity
  (P6); CloudFront cookie and `Origin` forwarding (P7); frontend deployment (P8); Playwright E2E (P16).

## P6 — Docker

- **Goal:** the whole application runs from one command, in containers we understand, behind one origin.
- **Build (P6a):** the backend multi-stage image: one image, the `api` command (default) and `migrate`
  (`alembic upgrade head`); non-root, pinned, small, with its own liveness healthcheck.
- **Build (P6b):** the frontend image (a Node build, then nginx serving the static export) and the
  Compose `app` profile: `db` → `migrate` → `api` → `web`. Plain `docker compose up -d --wait` stays
  database-only. The api is never published; only `migrate` holds admin credentials; `api` and `web` run
  read-only with no capabilities.
- **Build (P6c):** ADR 014 and the amendments to ADRs 006, 008, 011 and 013; this file, `the project notes`
  and `docs/mvp.md` brought in line; the manual check below.
- **Explain first:** layers, multi-stage builds, non-root users, why config and secrets are never baked
  into an image, why one image serves several commands, what a reverse proxy must not touch.
- **Done when:** `docker compose --profile app up --build --wait` gives working login and follow at
  `http://localhost:3000`, and the follow survives a refresh, an api restart and `down` then `up`.
  Automated: the container suite (`docker/tests`, 91 collected items) plus the backend and frontend
  suites. The manual run, including real Google sign-in through the containers, was done by the owner
  on 2026-09-21 (all twelve steps passed; recorded in ADR 014).
- **You should be able to answer:** why multi-stage? Why not bake config into the image? Why does only
  `migrate` get the admin credentials? Why is the api not published? What must nginx not touch, and
  what breaks if it does (Origin, cookies, error bodies)? Why do the Google settings use `${VAR:-}` and
  not `${VAR:?}`? What does the nginx container prove about production, and what does it not?
- **Left for later (ADR 014):** the `worker` command and its Compose service (P9); CloudFront rules,
  HTTPS and the production-mode cookie (P7, P8); image digest pinning, scanning and signing (P8); making
  nginx follow a recreated api container (P7 or P8, if the deployment needs it).

## P7 — Terraform

- **Goal:** the AWS stack from ADR 008, created and destroyed by code.
- **Before any provisioning:** the itemised cost table (purpose, running cost, cost while stopped, cost
  after destroy, verdict per service) for your approval.
- **Build:** VPC, RDS, ECR, ECS task/service, ALB, S3 buckets, CloudFront, IAM and OIDC, logs, secrets.
  Test `apply` and `destroy` end to end.
- **Explain first:** state, plan versus apply, why the pgvector extension is not Terraform's job, why
  Terraform ignores the task-definition image tag.
- **Done when:** apply gives a live health endpoint through CloudFront; destroy leaves nothing behind.
- **You should be able to answer:** what is Terraform state? What does destroy not remove?
- **Done 2026-09-22.** The whole product ran on AWS over HTTPS and a real Google sign-in completed.
  The design changed during P7e: the deployment is split by **lifetime**, not by layer
  ([ADR 015](decisions/015-persistent-edge.md)), because a recreated CloudFront distribution gets a new
  domain and that domain is the registered OAuth redirect URI. `infra/edge` (CloudFront, the static
  site, the shared origin secret) is never destroyed and costs nothing per hour; `infra/stack` still
  dies after every session at $1.41/day idle. Operating instructions: [docs/runbook.md](runbook.md).
- **Three bugs were found by real applies that every offline gate had passed:** the migration task's
  argv, `ALTER ROLE ... NOSUPERUSER` (legal locally, refused by `rds_superuser`), and a missing
  `COOKIE_SECURE`. Each now has a test, and the last has a guard that reads the backend's own
  `Settings` class. The lesson is in ADR 015.
- **Deferred:** re-pointing the edge at a rebuilt load balancer is unproven — the next spin-up is the
  test; the frontend is uploaded by hand until P8; `minimum_protocol_version` is stuck at TLSv1 while
  the default CloudFront certificate is used.

## P8 — CI/CD → Gate A

- **Goal:** merging to `main` deploys, and a bad change cannot.
- **Build:** GitHub Actions (lint, types, tests, build); OIDC to AWS; image build and push; migration
  task; ECS deploy with rollback; smoke test; frontend sync and cache invalidation. Also **test Google
  OAuth on the CloudFront URL**; a custom domain only if that proves necessary.
- **Explain first:** OIDC versus stored keys; what happens on a failed migration or deploy.
- **Done when:** a push deploys automatically; a deliberately failing test blocks it.
- **Gate A:** log in and follow stocks on the live AWS URL.
- **You should be able to answer:** what if a migration fails halfway? How does rollback work?

## P9 — Document ingestion core

- **Goal:** a real, safe ingestion pipeline (no AI yet).
- **Build:** upload API; `BlobStore` (filesystem locally, S3 in AWS); PDF text extraction with a
  permissively licensed library; content-hash dedupe; page-aware chunking; the Postgres jobs table and
  worker loop (claim with `SKIP LOCKED`, retries with backoff, leases); Documents page in the UI.
- **Explain first:** idempotency via unique constraints and `ON CONFLICT`; `SKIP LOCKED`; why no
  transaction spans network I/O.
- **Done when:** the same file uploaded twice or eight times at once gives one document and one set of
  chunks; a failing job retries then fails cleanly; a scanned PDF is rejected with a clear status.
- **You should be able to answer:** name three races and how each is closed. Why not FastAPI
  `BackgroundTasks`?

## P10 — Embeddings and retrieval

- **Goal:** find the right chunks, with citations preserved.
- **Build:** the embedder interface (fake in tests, Bedrock in real runs); embed chunks, reusing
  vectors by content hash; retrieval combining SQL filters with vector similarity and a deterministic
  rerank.
- **Explain first:** what an embedding is; cosine similarity; approximate versus exact search at our size.
- **Done when:** retrieval tests return the expected chunks with page numbers.
- **You should be able to answer:** why hybrid filtering? What breaks if we change the embedding model?

## P11 — Fact and event extraction, derived values

- **Goal:** turn documents into verified facts and events, and compute derived values (ADR 009).
- **Build:** LLM structured extraction over a fixed metric vocabulary; the deterministic validator
  (the quote must appear on the cited page and the number in the quote; units normalised by code);
  event tagging; pure functions for rolling sentiment, debt-to-equity and growth; Stock page with facts,
  events and sentiment.
- **Explain first:** why verify the model's output; compute-on-read; the debt-to-equity rules.
- **Done when:** extraction of a synthetic fixture is verified; a tampered quote is rejected; sentiment
  and ratio functions have unit tests including "insufficient data".
- **Decide here:** the policy when two documents disagree on a fact.
- **You should be able to answer:** how do you stop the model inventing a number? Why is debt/equity
  wrong for banks?

## P12 — Grounded chat (LangGraph) → Gate B

- **Goal:** answers where every claim is cited, or an explicit abstention.
- **Build:** the small graph: analyze → update_memory (stub) → retrieve → grade → generate → validate →
  respond; citation IDs and the validator (IDs exist, numbers appear in cited evidence, INR only);
  chat UI with a citation panel.
- **Explain first:** why a controlled workflow beats a free agent; the citation contract.
- **Done when:** tests for a grounded answer, an abstention, a retry after a failed validation, and an
  invented number being rejected.
- **Gate B:** cited RAG chat live on AWS.
- **You should be able to answer:** what does the validator prove and not prove?

## P13 — Investor memory

- **Goal:** the assistant remembers a small structured profile.
- **Build:** extraction from the user's own messages only; merge rules; the profile with supporting
  quotes; the embedding used to bias retrieval; the "what I remember" panel with edit and forget.
- **Explain first:** trust boundaries; why memory is never written from retrieved text.
- **Done when:** a prompt-injection fixture inside a document leaves the profile unchanged.
- **You should be able to answer:** how do you prevent memory poisoning?

## P14 — Deterministic matching → Gate C

- **Goal:** personalised results from rules, not from an LLM scoring each stock.
- **Build:** the matcher over facts (debt, dividend, growth, quality) and rolling sentiment; statuses
  *match / partial / no match / not enough data*; honest "not assessable" cases; the LLM writes only
  the explanation; Matches UI.
- **Explain first:** hard filters versus soft scores; why the rules live in code.
- **Done when:** golden tests for several profiles across the three stocks.
- **Gate C:** the full MVP works end to end.
- **You should be able to answer:** what is deterministic and what is the LLM's job?

## P15 — Scheduled RBI feed

- **Goal:** a genuine automated source and scheduled refresh.
- **Build:** the `FeedSource` for RBI RSS (lenient parsing, conditional requests, dedupe by canonical
  URL and title hash); the worker's timer enqueuing `poll_feed`; the `FEED_MODE=live|fixture` switch.
- **Done when:** repeated polls create no duplicates; fixture mode works offline.
- **You should be able to answer:** why is a timer inside the worker safe with several workers?

## P16 — Hardening, docs, demo rehearsal

- **Goal:** a defensible, demonstrable project.
- **Build:** per-user daily chat limit; a security pass (authorisation, injection, secrets); one
  end-to-end test; README; the `docs/` set; a demo script; a rehearsed `terraform destroy` and
  re-create.
- **Done when:** every acceptance criterion in [mvp.md](mvp.md) is demonstrably true.
- **You should be able to answer:** what breaks first at 100 times the data?

---

## Prerequisites you own

- **Before P2:** an AWS profile configured locally (no resources created). ✅ done (least-privilege CLI
  user with `aws login`; see ADR 010).
- **Before P9's demo data:** download the company documents (see the manifest, added in P9).
- **Before P8:** a Google Cloud OAuth client (testing mode) with your own account and any interviewer
  accounts you choose to add as test users. The company's two challenge test users are no longer needed.
