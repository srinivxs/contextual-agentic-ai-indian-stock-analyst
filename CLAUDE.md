# CLAUDE.md

Persistent project context for Claude Code. **Read this first in every session**, then inspect
the relevant code, and follow the decisions below. Do not contradict a documented decision
without explaining why and updating this file plus the relevant ADR. Keep this file concise.

## Project

- **Product:** Contextual Agentic AI Indian Stock Analyst. A personal equity-research assistant. A user
  logs in (Google OAuth), follows three stocks (RELIANCE, TCS, HDFCBANK), sees the filings the app ingests by itself,
  and chats with a LangGraph agent that gives **grounded, cited answers in ₹ (US$ only as reported)**, remembers their
  investment preferences, and matches the stocks to them.
- **Nature:** a **portfolio and interview project**, built from a written project brief (kept
  locally as the spec, untracked).
  **Not** a production SaaS or a comprehensive financial platform.
- **Guiding principle:** *meet the brief's requirements, but minimise the data, infrastructure, and
  complexity required to demonstrate them.* Small scope, real architecture, working product, easy to
  explain. Reduce the **scale**, never the core concepts (RAG with citations and abstention, memory,
  ingestion with idempotency and concurrency safety, LangGraph, vector search, Docker, Terraform, CI/CD, tests).
- **Owner goal:** be able to defend and explain every component in an interview without Claude.
- **Acceptance contract:** [docs/mvp.md](docs/mvp.md). **Phased plan:** [docs/roadmap.md](docs/roadmap.md).
- **Current phase:** M0, M1, the docs re-cut, P2 (Bedrock verification, ADR 010), P3 (database
  foundation, ADR 011), **P4** (authentication and sessions, ADR 012), **P5** (follow API and frontend
  shell, ADR 013), **P6** (containers and the local Compose stack, ADR 014), **P7** (Terraform and
  the live AWS deployment, ADR 015) and **P8** (CI/CD, ADRs 016 and 017) are done. **Gate A passed on
  2026-09-23:** Google sign-in and following a stock on the live AWS URL, with the stack brought up by
  `scripts/demo-up.ps1`. **P9** (document ingestion core, ADR 018) is **done locally**.
  **All AWS work from P9 on is deferred to one Go-live phase (GL)** before final testing (the
  owner's decision, 2026-09-23): Terraform may be written and tested offline, nothing is applied
  until GL. **P10** (embeddings and search, ADR 019) and **P11** (facts, events, derived values,
  ADR 020) are **done locally**. **P12** (grounded chat, ADR 021) is **done locally**: **Gate B
  passed locally on 2026-09-28** (live at GL). **P13** (investor memory, ADR 022) is **done
  locally**. **P14** (deterministic matching, ADR 023) and **P15** (the scheduled RBI feed, ADR
  024) are **done locally** (built in parallel; the owner delegated their design, 2026-09-29).
  **GL** (go-live on AWS) is **done**: the worker container, the documents bucket, Bedrock IAM,
  the switches as Terraform variables (ADR 008 amendment). First live session 2026-10-06:
  `demo-up.ps1` took 11.7 minutes, then the worker refilled the empty database from AWS (BSE,
  screener.in, RBI, S3, Titan and Nova all working). Next: P16.
  The README presents the project as AWS-deployed (the owner's 2026-10-06 instruction: no
  local-run claims on GitHub).
  Day-to-day AWS operation: [docs/runbook.md](docs/runbook.md).

## Working rules for Claude

1. Work inside `C:\Contextual Agentic AI Indian Stock Analyst` only. Never touch files outside it.
2. **Keep it explainable.** The owner must be able to explain every major component. Prefer plain,
   readable code; small functions; explicit over clever; few dependencies; no abstraction without a
   second user. Do not generate large systems at once. After each milestone give a short "how to
   explain it" summary. If something is too clever to explain simply, simplify it.
3. Small milestones (see the roadmap). Before coding, explain what we are building and list the files
   that will change. After coding, explain the important files and data flow.
4. TDD: tests first, run them red, then implement. Every major component has tests.
5. Show the git diff, then commit with a meaningful Conventional Commit message. One logical milestone
   per commit.
6. **Never push, merge, or force-push unless the user explicitly asks.** The GitHub repo is
   **public** (`origin` = `srinivxs/contextual-agentic-ai-indian-stock-analyst`; history rewritten
   on 2026-10-07, so commit IDs before that date no longer exist).
7. Challenge weak architecture; do not agree by default. Document important decisions as ADRs.
8. Do not use an LLM for work that deterministic code or the database can do reliably.
9. **No scraping of content, no anti-bot workarounds, no invented data.** The one exception is
   each stock's screener.in company page, read once a day under ADR 018's rules (switch,
   allow-list, honest User-Agent, nothing behind a login): its official BSE filing links
   (ADR 018) and, by the owner's decision in P11, its fundamentals table (ADR 020), each figure
   cited to its section, row and column. Do not reopen data-source research.
10. Do not spend the owner's money (AWS, data providers) without an explicit, itemised approval.
11. Before every commit inspect staged files for secrets and junk (keys, `.env`, tfstate,
    `node_modules`, venvs, downloaded documents, data files).

## Architecture (summary; ADRs hold the reasoning)

- **Shape:** a modular monolith. One Python backend package, one Docker image, two entrypoints (`api`,
  `worker`). No microservices.
- **Frontend:** **Next.js** static export on **S3 + CloudFront**, no running frontend container in AWS (ADR 006). No SSR,
  route handlers, or middleware; no dynamic ticker routes (use `?symbol=`); all data via client-side
  calls to `/api/v1`. An nginx image serves the export in the local Compose stack as a stand-in for
  CloudFront + S3 (ADR 014); it is not deployed.
- **Backend:** FastAPI (ADR 002), Pydantic, SQLAlchemy 2 async + asyncpg, Alembic. API under `/api/v1`;
  `/api/healthz` (liveness) and, from P3, `/api/readyz` (database).
- **Auth:** Google OIDC authorisation-code flow with PKCE, `state`, `nonce`; the backend issues its own
  server-side session (opaque token, hashed in the DB, HttpOnly Secure SameSite=Lax cookie). Identity is
  Google `sub`. Sign-in is invite-only: `ALLOWED_EMAILS` (from `.env`; required on AWS) is checked
  after Google verifies the account and before anything is stored. Same-origin via CloudFront, so no CORS. Details, the invariant → test map and the
  known limitations are in [ADR 012](docs/decisions/012-authentication-and-sessions.md).
- **Database:** PostgreSQL 16 + pgvector (ADR 001) for everything: users, sessions, stocks, follows,
  documents, chunks and embeddings, extracted facts, events, investor profiles, conversations, and the
  job queue. **The schema grows one phase at a time**; nothing is designed up front.
- **Containers (ADR 014):** one backend image (default command `api`; `migrate` runs `alembic upgrade
  head`; `worker` runs `python -m app.worker`) and one frontend image (nginx serving the static
  export). Compose profile `app` starts `db` → `migrate` → `api` + `worker` → `web` on
  `127.0.0.1:${WEB_PORT:-3000}`; only the worker has the `blobs` volume; plain `docker compose up
  -d --wait` stays database-only. Only `migrate` holds admin credentials, the api is never published,
  there is no `env_file`, nothing secret goes in an image, and `api` and `web` run read-only with no
  capabilities. Local only: P6 adds no ECR, ECS or AWS.
- **Data sources (ADR 007, amended by ADR 018):** (1) **Official filings, fetched automatically**:
  once a day per stock the worker reads the stock's screener.in company page for links only, keeps
  PDFs on `www.bseindia.com` (every transcript and presentation of the last `FILINGS_YEARS=3`
  years, the last 3 annual reports and the recent announcements: about 85), downloads each from
  its direct BSE file address and ingests it, cited to the filing and page. Switched by
  `FILINGS_DISCOVERY` (off by default). From P11 the same page's fundamentals table is parsed by
  code into cited facts (ADR 020), and since 2026-09-30 its "top ratios" list (market cap,
  P/E, book value, dividend yield, ROE, face value) feeds the stock page's Fundamentals card
  (`screener_ratios`, migration `0013`; ADR 020 amendment); nothing else of screener's is stored
  or shown.
  No user uploads (removed in P9d; the brief asks the app to ingest by itself). (2) **RBI press-release RSS** (ADR 024): the
  automated news feed, `FEED_MODE=fixture` by default (synthetic items, offline), `live` a polite
  conditional GET polled by the worker's timer; an item becomes a stock event only when it names
  the company. (3) **End-of-day share prices (ADR 025, the owner's 2026-09-29 decision, amending
  ADR 007):** BSE's daily price file ("bhavcopy"), only the three stocks' rows, fetched a few
  files per run with a long pause and stopping at BSE's first 406 ("slow down"), switched by
  `PRICES_ENABLED` (off by default); bonus issues and splits adjusted on read from BSE's previous
  close; no live prices, no indices. Events come from real documents, never hand-written notes.
- **Source content vs derived facts:** documents are stored as source content; a fact (for example
  revenue) is our own record carrying document, page, and a verbatim quote. Original files are never
  served back to other users; the UI shows facts and short excerpts (about 300 characters) plus a link.
- **Ingestion pipeline:** receive/fetch → normalise (PDF text per page; scans rejected) → dedupe (file
  SHA-256; feed canonical URL + title hash) → page-aware chunk → embed (reuse by content hash) → store →
  derive (LLM extracts facts from a **fixed metric vocabulary** and tags events; a **deterministic
  validator** requires the quote to appear on the cited page and the number in the quote).
- **Jobs (ADR 005):** Postgres table; types `ingest_document`, `discover_filings`, `fetch_filing`,
  `embed_document`, `extract_document`, `poll_feed` (P15; one per time slot); statuses pending,
  processing, completed, failed; `SKIP LOCKED` claims, leases, retries with backoff. Lanes (2026-10-07):
  one web lane (BSE/screener jobs, one at a time) + `WORKER_AI_LANES` (2) AI lanes; `claim_next(kinds=...)`. Concurrency safety
  comes from unique constraints, `ON CONFLICT`, and a partial unique index on the job dedupe key.
- **Derived values are computed on read (ADR 009):** rolling sentiment, debt-to-equity, and growth are
  pure functions over stored facts and events, never stored. Debt/equity is "not applicable" for banks
  and "not assessable" when inputs are missing or on different bases.
- **RAG and citations:** retrieval = SQL filters + vector similarity + deterministic rerank. Evidence
  items get IDs (`N#` document chunk, `F#` fact, `D#` derived value, `E#` event, `M#` match verdict). The LLM returns claims with
  citation IDs; deterministic code verifies the IDs exist, every number appears in the cited evidence,
  and every amount keeps the currency of its cited evidence (never converted). A render step resolves IDs to real sources; the LLM never invents a URL.
  If evidence is insufficient the answer is "I don't have that in the data."
  **One figure per thing (ADR 021 amendment, 2026-09-29):** each measure in an answer comes from
  one source (the best-ranked one that has every year needed); other sources' differing figures
  are disclosed with their own source by code; `conflicting_figures` refuses two figures for one
  stock, measure, period and basis; the table shows only when it agrees. A plain figure question
  is answered by code (`app/chat/lookup.py`, no LLM); a cause must cite a filing passage or
  event; a future share price is refused by code; "out of scope" only when none of our three
  stocks is named. Second amendment (same day): other companies are refused before retrieval,
  and a question borrows a stock from the conversation only when it refers back ("its", "and ...");
  each figure carries what its source calls it ("Sales", "Revenue from Operations"); news
  sentiment is never evidence about results; valuation, memory and "where from?" are answered
  by code (`app/chat/code_answers.py`). Third amendment: "revenue" is revenue only (a missing
  figure is said to be not available, never replaced); a figure of no named company, or a
  comparison with no measure, is asked back with clickable choices (live reply only, not
  stored); "compare it with X" keeps the earlier stock and measure.
- **LangGraph (ADR 003):** one small explicit workflow: analyze → update_memory → a route by the
  question's **intent** (`app/chat/understand.py`): remembered, out_of_scope (another company,
  found by `app/chat/entities.py` before any retrieval), recall, forecast, sources, no_profile, or
  retrieve → grade (→ lookup or valuation by code; else abstain) → generate → validate (retry
  once) → respond. No checkpointer, no MCP.
- **Memory (ADR 022):** a small structured profile `{risk_preference, debt_preference,
  investment_style, other_preferences}` from a **fixed vocabulary**, each field keeping the user's
  supporting quote; editable and deletable by the user. **Read by code, not an LLM**, and only from
  the user's current message (first-person sentences, no questions, quotes removed, simple
  negation, requests for information skipped), never from retrieved text, events, history or
  replies. A message that only states preferences gets a fixed "Noted. I'll remember" reply
  (status `remembered`, no LLM call); "what do you remember?" is read back by code. One
  embedding of the profile summary nudges retrieval (at most 0.02, like recency).
- **Matching (deterministic, ADR 023):** stated preferences become hard filters (avoid high debt:
  debt to equity ≤ 1.0) and soft criteria (conservative ≤ 0.5 and no profit fall; dividend paid;
  growth or aggressive ≥ 10%; return on equity ≥ 15%; momentum = six-month **price** return ≥ 0,
  else earnings momentum; value = P/E ≤ 20 (not assessable after a bonus or split since the EPS
  year); short term = one-year volatility ≤ 30%; long term = profit did not fall over three
  years) over stored facts and prices, plus negative rolling sentiment as a caution. Result per
  stock: *match / partial / no match / not enough data*, each reason with its figure and
  citations. Reasons no stock can be judged on are shown once, above the cards. The Match page uses no LLM; in the chat the verdicts are `M#` evidence and the
  LLM only writes the explanation.
- **AWS (ADR 008, amends 004):** CloudFront → S3 (frontend) and ALB → **one ECS Fargate service, one
  task, two containers (`api`, `worker`)**; RDS PostgreSQL + pgvector in private subnets; a private S3
  bucket for fetched filings (`BlobStore` interface: filesystem locally, S3 in AWS); Bedrock via the
  task IAM role; SSM/Secrets Manager; CloudWatch Logs. No NAT, no EventBridge, no SQS. The worker owns
  a small timer that enqueues `poll_feed`.
- **Deployment is split by LIFETIME, not by layer (ADR 015, amends 008; ADR 016):** four Terraform
  roots. `infra/bootstrap` (state bucket), **`infra/cicd`** (ECR, the GitHub OIDC provider and a
  push-only CI role; ADR 016) and **`infra/edge`** (CloudFront, the private S3 site + OAC, the
  CloudFront Function, the shared `X-Origin-Verify` secret) are applied once and **never destroyed** --
  they have no hourly rate, so keeping them is free and the `*.cloudfront.net` domain stays permanent,
  which is what lets the Google OAuth redirect URI be registered once. `infra/stack` (VPC, ALB, ECS,
  RDS, the documents bucket, IAM) is destroyed after every session at $1.41/day idle, about
  $2.30/day running (api + worker, 0.5 vCPU / 2 GB). The roots exchange values one way
  each: persistent → ephemeral through SSM parameters (`origin_verify`, `public_base_url`, read in
  `infra/stack/edge.tf`; `ecr_repository_url`, read in `infra/stack/registry.tf`), ephemeral →
  persistent through `-var alb_origin_domain=...`, because a data source must never point at
  something destroyed nightly. A plan of `infra/stack` fails if `infra/edge`
  or `infra/cicd` was never applied; that is intended.
- **CI/CD (ADRs 016, 017):** `.github/workflows/pipeline.yml` runs on **every push to `main`** (no
  PRs in this solo project). Three check jobs (backend with a real Postgres via Compose, frontend,
  Terraform offline + repo-level tests) never see AWS; then `image` (push-only role) pushes
  `:<commit sha>` to ECR (tags IMMUTABLE); then `deploy` (deploy role) runs
  `.github/scripts/deploy_backend.py`: stack down → nothing; stack up → new revisions by digest,
  **migration as a one-off task first**, `update-service`, rollback detection, `/api/readyz` through
  CloudFront; then the frontend is synced and invalidated. Both roles trust only GitHub's **immutable
  OIDC subject** `repo:<owner>@<id>/<name>@<id>:ref:refs/heads/main`. Actions are pinned by SHA.
  `infra/stack` runs the newest image by digest (`data.aws_ecr_image`, `most_recent`).

## Technology stack

| Area | Choice | Purpose |
|---|---|---|
| Backend | Python 3.11, FastAPI, Uvicorn, Pydantic (+ pydantic-settings) | API, validation, config |
| DB access | SQLAlchemy 2 async, asyncpg, Alembic | Queries, migrations |
| Database | PostgreSQL 16 + pgvector on RDS | Relational + vector + queue |
| Agent | LangGraph (LangChain only for thin LLM wrappers) | Chat workflow |
| LLM / embeddings | **Amazon Bedrock**, IAM auth, no API keys | Extraction, tagging, generation, embeddings |
| PDF text | pypdfium2 (BSD-3 / Apache-2.0; PyMuPDF is AGPL, so avoided) | Page-level text |
| Frontend | Next.js (static export), React, TypeScript | UI, built to static files |
| Tooling | uv (lockfile), ruff, mypy, pytest, pytest-asyncio, httpx; ESLint, tsc, Vitest | Quality gates |
| Infra | Terraform, ECS Fargate, ALB, CloudFront, S3 (x2), RDS, ECR, SSM, CloudWatch | Deployment |
| CI/CD | GitHub Actions | Automation |

Decided in [ADR 010](docs/decisions/010-bedrock-models-and-region.md): region **ap-south-1**; embeddings
`amazon.titan-embed-text-v2:0` at **1024 dimensions** (`vector(1024)`); chat and extraction **Amazon Nova 2
Lite** via `global.amazon.nova-2-lite-v1:0` (backup: Nova Pro). Claude on Bedrock is blocked on this account
(Marketplace payment check) and is not used. Model IDs and region live in `Settings`, never hardcoded.
Nova 2 Lite's extraction was checked in P11 (kept, with definition guards; ADR 020) and its chat
in P12 (kept: grounded, cited answers on real questions once the checker was tightened; ADR 021).

## Development rules

- **Python:** full type hints, `ruff` + `mypy` clean, async for I/O, wrap sync SDKs (boto3) in
  `asyncio.to_thread`. LLM, embedder, and blob store sit behind small interfaces so tests use fakes.
  Config via one `Settings` object; fail fast at startup on missing config.
- **API:** `/api/v1`, JSON, Pydantic in and out, cursor pagination, idempotent verbs where the semantics
  allow (`PUT`/`DELETE` for follows), one error envelope `{error:{code,message,request_id[,details]}}`
  (`details` only on 422, never echoing raw input), ticker path params validated against
  `^[A-Z0-9&-]{1,20}$`. User-owned resources: foreign IDs return 404.
- **Database:** schema changes **only** via Alembic (expand/contract; forward-only), **one migration per
  phase for that phase's tables**. UUID for users, sessions, conversations, messages; identity bigint
  for stocks, documents, chunks. All timestamps `timestamptz` UTC. Idempotency comes from unique
  constraints and `INSERT ... ON CONFLICT`. **No transaction spans a network call.** The runtime DB role
  has no DDL rights.
- **Errors:** domain exceptions map to HTTP statuses in one place. Never leak stack traces or secrets.
  Structured JSON logs with `request_id`; never log tokens, full prompts, or document text.
- **Testing:** `tests/unit` (pure logic), `tests/api` (in-process ASGI via `httpx`), `tests/integration`
  (real Postgres, from P3). Shared helpers in `tests/helpers.py`. TDD. Minimum 80% coverage plus one
  end-to-end flow. Fake LLM/embedder in tests. Include concurrency and prompt-injection tests. Container
  tests live in `docker/tests` (own `pytest.ini`, not part of the backend run).
- **Fixtures:** synthetic only (fictional `DemoCo`, ticker `DEMO`). **Never associate synthetic
  numbers with RELIANCE, TCS, or HDFCBANK.** Real documents live in git-ignored `data/local/`; the repo
  carries a manifest (title, publisher, URL, date) but never their content.
- **Security:** no secrets in git; env / SSM / Secrets Manager; least-privilege IAM; parameterised SQL
  only; retrieved text is untrusted data; outbound fetches only to allow-listed hosts.
- **Git:** solo project. Small, milestone-sized commits made directly on `main` (no feature branches or
  pull requests). **Never push without the owner's explicit approval.** Conventional Commits (`feat:`,
  `fix:`, `chore:`, `ci:`, `infra:`, `docs:`, `test:`). No meaningless messages.
  **No AI co-author trailer** (`Co-Authored-By:`) and no "Generated with" line on any commit:
  commits carry only the owner's name (the owner, 2026-10-07). Never name the brief's company,
  and never write the live site address in the repo (read it from `terraform output
  public_base_url` in `infra/edge`).

## Architectural decisions

| # | Decision | Reason | Tradeoff |
|---|---|---|---|
| [001](docs/decisions/001-postgresql-pgvector.md) | PostgreSQL + pgvector | One transactional store | Scale ceiling (irrelevant here) |
| [002](docs/decisions/002-fastapi.md) | FastAPI | Async, Pydantic, DI | Async discipline required |
| [003](docs/decisions/003-langgraph.md) | LangGraph, small controlled workflow | Inspectable grounding path | Extra dependency for a fixed pipeline |
| [004](docs/decisions/004-ecs-fargate.md) | ECS Fargate, minimum footprint, no NAT (partly superseded by 008) | Cheap, destroyable, real AWS | Public task IPs; CloudFront to ALB over HTTP |
| [005](docs/decisions/005-postgres-job-queue.md) | Postgres jobs table | Transactional enqueue, fewer services | Polling, home-grown leases |
| [006](docs/decisions/006-nextjs-static-export.md) | Next.js static export on S3 + CloudFront | No frontend server to run | No SSR or dynamic ticker routes |
| [007](docs/decisions/007-source-strategy.md) | User documents + RBI feed; no prices; source compliance record | Terms and access ruled out the obvious sources | Thin events; no value/momentum |
| [008](docs/decisions/008-minimal-aws-architecture.md) | One Fargate task, two containers; private docs bucket; no EventBridge | Smallest real AWS footprint | Shared CPU; worker crash restarts the API |
| [009](docs/decisions/009-compute-on-read.md) | Derived values computed on read | Consistent by construction, easy to explain | Must materialise at larger scale |
| [010](docs/decisions/010-bedrock-models-and-region.md) | ap-south-1; Titan V2 (1024 dims); Nova 2 Lite chat; no Claude | Verified callable, no Marketplace gate, cheap | Chat quality unproven; chat inference is global, not India-only |
| [011](docs/decisions/011-database-access-and-migrations.md) | Two DB roles (migration / no-DDL runtime); hand-written Alembic; `MIGRATION_DATABASE_URL` kept out of `Settings`; seed inside the migration; on RDS the runtime role is created by a one-off `provision` task, not by an init script or a migration (amended P7) | Runtime cannot reshape the DB; reproducible schema and seed | Superuser locally vs `rds_superuser` on RDS; default privileges also cover `alembic_version`; one more task to run on a cold start |
| [012](docs/decisions/012-authentication-and-sessions.md) | Google OIDC code flow + PKCE, verified ID token; server-side sessions (SHA-256 of a 256-bit token, fixed 7 days); `session` / `__Host-session` cookies; SameSite=Lax + Origin check | Nothing sensitive stored, revocable logout, every guarantee has a named test | No rate limiting or CSP yet; login-cookie replay is stopped by Google's single-use code, not by us; JWKS stale-refresh cooldown deferred |
| [013](docs/decisions/013-follow-api-and-frontend-shell.md) | Plain SQL (no ORM); `user_follows` keyed by (user, stock); idempotent `PUT`/`DELETE` follow returning 204, checks in the order Origin, session, validation; static-export frontend with a dev-only `/api` rewrite so the browser sees one origin (no CORS) | Concurrency safety from the database; one origin locally and in production | Client-rendered pages; production shape untestable until P6/P7; ESLint held at 9 |
| [014](docs/decisions/014-containers-and-local-compose.md) | Backend image (`api`, `migrate`) and an nginx image for the static export; Compose profile `app` (`db` → `migrate` → `api` → `web`), database-only by default; only `migrate` holds admin credentials; api unpublished; read-only, no capabilities; `${VAR:-}` for app-only secrets | One command runs the app; the images P7 and P8 will ship; one origin locally | nginx is not CloudFront; nginx resolves `api` once (restart `web` after recreating the api); tag-pinned, not digest-pinned; Google sign-in through nginx verified by hand only |
| [015](docs/decisions/015-persistent-edge.md) | Split the deployment by lifetime: a permanent `infra/edge` (CloudFront + private S3 + OAC + Function + origin secret) and an ephemeral `infra/stack`; `/api/*` uncached; no distribution-wide error responses; `app_env=production` with `COOKIE_SECURE` derived | A recreated distribution gets a new domain, which is the registered OAuth redirect URI; the edge has no hourly rate, so keeping it is free | Three roots and two applies per cold start; ADR 008's "all destroyable" now means "all that costs money"; TLSv1 forced by the default certificate |
| [016](docs/decisions/016-cicd-registry-and-github-identity.md) | A permanent `infra/cicd` root: ECR (moved out of the stack; the stack reads its URL from SSM), the GitHub OIDC provider, a push-only CI role trusted only for this repo's `main` (`StringEquals` on `aud` and `sub`); deploy is a separate role (P8c) | CI needs somewhere to push while the stack is down; no stored keys | Four roots; one OIDC provider per account (import if one exists); a job using a GitHub `environment:` gets a different `sub` and is refused |
| [017](docs/decisions/017-deploy-pipeline.md) | Deploy job with its own role: stack down → nothing; stack up → migrate as a one-off task first, `update-service`, detect rollback, `/api/readyz`; frontend only after the backend | A failed migration never touches the running service; "access denied" never passes for "stack down" | Migration runs on every deploy; CI revisions outlive destroy; `Describe/RegisterTaskDefinition` accept only `*` |
| [018](docs/decisions/018-automatic-official-filings.md) | The worker reads each stock's screener.in page for official BSE filing links once a day and fetches the PDFs from BSE (3 years: transcripts, presentations, annual reports, announcements; about 85); switch off by default; allow-listed addresses and redirects; honest User-Agent | Documents stay current with no manual step (uploads removed in P9d); citations point at exchange filings | screener's terms (personal use) are a stated, owner-accepted risk; layout changes find nothing rather than break |
| [019](docs/decisions/019-embeddings-and-retrieval.md) | Titan V2 fingerprints keyed by (model, chunk-text SHA-256); a worker timer queues `embed_document` for anything missing; hard token cap (`EMBEDDING_TOKEN_BUDGET`, spend = `sum(input_tokens)`); `EMBEDDINGS_ENABLED` off by default; exact cosine search (no index) + deterministic rerank (recency ≤ 0.02, ≤ 2 per filing, dedupe); locally a temporary pass from `stock-analyst-cli` only | Pay once per text; explainable ranking; nothing spends unless switched on | Local pass lasts ~15 min; account quota ~300k tokens/min |
| [020](docs/decisions/020-facts-events-and-derived-values.md) | Fixed 10-metric vocabulary; facts from filings by Nova 2 Lite + a deterministic validator (with definition guards), and from screener.in's table by code; each fact cites page + quote or section/row/column; money as reported, never converted; conflicts resolved on read (annual report > screener > presentation > call > announcement; >1% apart = disputed); events + rolling sentiment; derived values on read; `EXTRACTION_ENABLED` off by default; cap `EXTRACTION_BUDGET_USD` | Numbers must be provable; the LLM never writes the database | About 1 in 10 accepted facts can be a wrong column or entity; the strict checker refuses many true figures |
| [021](docs/decisions/021-grounded-chat.md) | A fixed LangGraph workflow; numbered evidence (F#, D#, N#, E#; passages and events in `<document>` tags); one forced tool call; a code checker (cited, at most 5 IDs, IDs exist, numbers in the cited evidence, currency kept, a judgment shows its figures, one measure over time from one source, no URLs; amended 2026-09-29: one figure per thing per answer, causes only from documents) with one retry, else abstain; render is code; plain figure questions answered by code; `CHAT_ENABLED` off, `CHAT_BUDGET_USD` $1 | The model only writes sentences; every shown number is in a stored row | No entailment check; a cited passage vouches for its own numbers |
| [022](docs/decisions/022-investor-memory.md) | A fixed-vocabulary profile read by code (not an LLM) from the user's own current message only: first-person sentences, no questions, quotes removed, negation; newer statement replaces a field; `remembered` reply with no LLM call; profile as context (labels, never cited); profile fingerprint nudges search ≤ 0.02 | Memory poisoning is impossible by construction; free and testable | Unusual phrasing is not recognised (the panel is the fallback) |
| [023](docs/decisions/023-deterministic-matching.md) | Matching rules in code: hard filter (avoid high debt: D/E ≤ 1.0) and soft criteria (conservative, dividend, growth, quality, stability); status match / partial / no match / not enough data; negative sentiment only cautions; value, momentum, horizon not assessable; Match page without an LLM; chat explains `M#` verdicts under the checker | Repeatable, provable, not advice by another name | Thresholds are judgment calls; no prices, so no yield, value or momentum |
| [024](docs/decisions/024-scheduled-rbi-feed.md) | RBI RSS: `FEED_MODE=fixture` by default; live = polite conditional GET; lenient safe parsing (no DOCTYPE/ENTITY to ElementTree); unique (source, canonical URL) and (source, title hash); a worker timer queues `poll_feed` once per time slot via the jobs dedupe key; events only for items naming a company, tagged by code | No duplicates under repeated or concurrent polls; offline by default; no LLM | Few stock events (most releases name no company); feed terms ambiguous (ADR 007) |
| [025](docs/decisions/025-end-of-day-prices.md) | End-of-day prices from BSE's daily price file (owner's choice over Alpha Vantage or none): three stocks' rows, a few files per run, stop at the first 406; `PRICES_ENABLED` off by default; bonus/split adjustment on read from BSE's previous close; unlocks price momentum, P/E value, volatility horizon, price charts, chat price answers | Official exchange data, free, cited per day | BSE's terms unreadable (owner-accepted risk); BSE keeps ~1 month of files, so history grows a day at a time; end of day only |

Other decisions (no ADR): no LangMem; app secrets in SSM Parameter Store;
Bedrock-only with no API-key fallback unless Bedrock is unavailable; pure-ASGI middleware and a
`create_app()` factory (`uvicorn --factory`); request IDs via `ContextVar` + `request.state`.

## Important constraints (must not be broken)

- Every AI-generated factual claim is grounded in retrieved data and cited to a stored source.
- Citations reference real stored rows; the LLM never invents a URL, number, or source.
- The system never invents financial data. If it is not in the data: "I don't have that in the data."
- Money is stored and shown in the currency the filing reports, **never converted** (no FX data):
  INR in crore, US$ in millions, always labelled. Derived values and comparisons combine one
  currency only (ADR 020; the owner's P11 decision, replacing "all money is INR").
- Ingestion is idempotent; concurrent or repeated ingestion of the same document creates no duplicates
  or inconsistent state. Derived values are computed on read, never incremented or cached.
- Chat memory is written only from the user's own messages.
- Stored document files are never served back; users get metadata and the official BSE link.
- Secrets are never committed. AWS auth uses IAM roles and OIDC, not access keys.
- Everything AWS is Terraform-managed and cleanly destroyable (rules in ADRs 004 and 008).
- Containers: never bake configuration or secrets into an image, never use `env_file`, never publish the
  api to the host, and only `migrate` gets admin credentials (ADR 014). Compose is for local use only.
- **Forbidden unless a concrete MVP requirement proves otherwise:** Kubernetes/EKS, Redis/ElastiCache,
  Kafka, OpenSearch, DynamoDB, SQS, EventBridge, microservices, multi-region, autoscaling, paid
  monitoring, NAT Gateway, custom domain (test Google OAuth on the CloudFront URL first), scraping
  content (the screener.in company page under ADR 018 and ADR 020 is the only exception),
  additional data providers, more than three stocks.
- Do not add technologies or change architecture without documenting the decision.
- The recommender is not investment advice; the UI must say so.

## Current state

- **Done:** M0 governance; M1 backend skeleton (`create_app()` factory, typed `Settings`, JSON logging,
  request-ID middleware, one error envelope, `GET /api/healthz`; 57 tests, 100% coverage, ruff and mypy
  strict clean); `.gitattributes`; ADRs 001–009; P0/P0b data-source spike (closed, conclusions in ADR
  007); docs re-cut around the MVP (`docs/mvp.md`, `docs/roadmap.md`); P2 Bedrock verification (ADR 010);
  P3a: docker-compose PostgreSQL 16 + pgvector (pinned image) with a migration role and a no-DDL runtime
  role, async SQLAlchemy engine/pool/session (`app/db/engine.py`), `GET /api/readyz` (503 when the
  database is down; `/api/healthz` stays dependency-free); P3b: Alembic (`backend/migrations/`,
  revision `0001`), the pgvector extension, the `stocks` table seeded with exactly RELIANCE, TCS and
  HDFCBANK, migration up/down/up tests; P4 (ADR 012, migration `0002`): `users` and `sessions`,
  `GET /api/v1/me`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/google/login` and `/callback`
  (PKCE, signed `oauth_login` cookie, RS256 ID-token verification with a JWKS cache, one short
  transaction after authentication), verified end to end against the real Google OAuth service.
  P5 (ADR 013, migration `0003`): `user_follows`, `GET /api/v1/stocks`, idempotent
  `PUT`/`DELETE /api/v1/stocks/{symbol}/follow` (plain SQL, no ORM), and the Next.js static-export
  frontend in `frontend/` (sign-in page and a Stocks page with follow toggles; Vitest, ESLint, real
  `next build`). P6 (ADR 014): the backend image with `api` and `migrate` commands (P6a, plus the `httpx`
  runtime-dependency fix found by its first run), the frontend nginx image and the Compose `app` profile
  (P6b), and ADR 014 with the manual check (P6c). Container suite: 91 items in `docker/tests`; 25 + 55
  deliberate breakages, all caught (8 re-run in P6c). Backend: 560 tests, 100% coverage, ruff and mypy strict clean.
  Frontend: 77 tests.
  P7 (ADR 015): four Terraform roots' worth of infrastructure, applied and destroyed for real.
  `infra/bootstrap` (state bucket), `infra/preflight` (credential check), **`infra/edge`** (permanent:
  CloudFront (its `*.cloudfront.net` address is `terraform output public_base_url`), the private site bucket, the origin secret) and
  `infra/stack` (51 resources then, 49 since P8a moved the registry out; destroyed after every
  session). Verified live on 2026-09-22: static site over HTTPS, `/api/readyz` 200 (so the
  `stock_app` runtime role really works), 401 without a session, real Google sign-in, and the session cookie named **`__Host-session`** with `Secure` -- the first time
  that prefix has ever been exercised. Terraform: stack 55 tests, edge 15, hygiene 84, plus a Node test
  for the CloudFront Function.
  P8 (ADRs 016, 017): `infra/cicd` (ECR, GitHub OIDC, push and deploy roles), the pipeline and the
  deploy script, `scripts/demo-up.ps1` / `demo-down.ps1`, isolated subnets in every zone (RDS once had
  capacity only in ap-south-1c). Tests: backend 560 (100% coverage), frontend 77, Terraform stack 59,
  edge 16, cicd 21, repo-level 147 (hygiene, workflow, deploy script, demo scripts).
  P9 (ADR 018, migrations `0004`, `0005`; done locally): `documents`, `document_pages`, `chunks`,
  `jobs`; the worker (`python -m app.worker`: `SKIP LOCKED` claims, leases, attempt number as the
  fencing token, backoff 30 s doubling to 900 s); pypdfium2 page text (scans rejected with a clear
  reason); page-aware chunks (1,200 characters, never across pages); automatic BSE filings via
  screener.in links (three years; direct `AttachHis`/`AttachLive` addresses; honest User-Agent,
  2 s pause, allow-listed hops); a follow checks a stock at once, at most once an hour per stock
  (since 2026-09-30 the **Update data** button in the top bar checks every stock, prices and the
  RBI feed in one click, `/api/v1/data/refresh`, at most once an hour for everyone (`data_refreshes`,
  migration `0013`), and every page shows the date the data is updated to, `/api/v1/data/status`;
  ADR 018 amendment); the Documents
  page (tabs per stock, grouped by kind). **No uploads** (removed; the brief asks the app to
  ingest by itself). Local result: 77 of 81 reachable filings, 5,376 pages, 16,232 chunks.
  Tests: backend 806 (100% coverage), frontend 126, containers 97, repo-level 147.
  P10 (ADR 019, migration `0006`; done locally): `embeddings(model, content_hash, vector(1024),
  input_tokens)`; `app/embeddings.py` (Bedrock Titan V2 via boto3 in a thread, adaptive retries);
  `app/embedding_jobs.py` (timer + `embed_document`, batches of 32, keeps paid work, token cap);
  `app/retrieval.py` + `GET /api/v1/search` (exact cosine, rerank, ~300-char excerpts, page links);
  a **Search the filings** box per stock. First real run: 15,006 of 16,496 texts, 3.38M tokens
  (about $0.07); the rest finish on the next run with a pass. Tests: backend 887 (100%), frontend
  149, containers 100, repo-level 147.
  P11 (ADR 020, migration `0007`; done locally): `facts`, `events`, `extraction_calls`; the fixed
  vocabulary (`app/vocabulary.py`), page selection, the Nova 2 Lite extractor behind a small LLM
  interface (`app/llm.py`, `app/extraction_prompts.py`, `app/extraction_jobs.py`, job
  `extract_document`), the deterministic validator with definition guards
  (`app/fact_validation.py`), screener.in's table parsed by code (`app/screener_numbers.py`,
  `app/screener_facts.py`), derived values on read (`app/derived.py`), the free re-check
  (`python -m app.recheck_facts`), `GET /api/v1/stocks/{symbol}/insights` and the
  `/stock/?symbol=` page with citation chips. Real run on 84 filings: 225 calls, $0.49 (cap $2);
  stored 93 filing facts, 272 screener.in figures, 88 events. The hand check of 20 facts found 3
  wrong by the owner's test, so the rule pointed to Nova Pro; the owner chose free definition
  guards instead. Tests: backend ~1,580 (100%), frontend 251, containers 101, repo-level 147.
  P12 (ADR 021, migration `0008`; done locally, Gate B passed locally): `conversations`, `messages`; `app/chat/`
  (understand, evidence, answer_check, render, prompts, graph, store), `app/insights_store.py`,
  `/api/v1/chat/messages` and `/chat/conversations` (a user may delete their own, with its
  messages, since 2026-09-30), the `/chat/` page; langgraph 1.2.
  P13 (ADR 022, migration `0009`; done locally): `investor_profiles` (one row per user and field,
  vocabulary-checked tags, quote, source chat/edited); `app/memory/` (vocabulary, extract, store),
  `/api/v1/profile` (GET, PUT and DELETE per field, DELETE all), the "What I remember" panel on the
  Chat page, the not-investment-advice note, and the injection test (a first-person filing
  passage leaves the profile unchanged).
  P14 (ADR 023; done locally): `app/matching/` (model, rules; golden tests across synthetic stocks),
  `GET /api/v1/match`, the Match page, "Match me" in the chat (`M#` evidence).
  P15 (ADR 024, migration `0010`; done locally): `feed_items`, `feed_state`, feed events
  (`events.feed_item_id`); `app/feeds/` (rbi, tagging, store), `app/feed_jobs.py`, the worker's
  poll timer, `GET /api/v1/feed`, the "RBI press releases" tab, citation source `rbi`.
  Redesign (2026-09-29, the owner's mockup): Plus Jakarta Sans, a neutral palette with light and
  dark modes and a switch in the top bar (the owner dropped the first green theme; tokens at the
  top of `frontend/src/app/globals.css`, `ThemeToggle`, `lib/theme.ts`; ADR 013 amendment), a left menu and a top bar with a stock
  finder (`AppShell`, `StockJump`, `Icons`, `Monogram`), a Home dashboard at `/home/` (sign-in
  lands there) with net profit charts from `GET /api/v1/stocks/{symbol}/series` (screener.in,
  consolidated, full years only: one source, like with like), and the other pages restyled.
  The mockup's index cards, publisher news and "what should I buy" were replaced with real,
  cited content, and no advice is given. Prices (ADR 025, below) later filled the price chart
  and watchlist the mockup showed.
  Prices (ADR 025, migration `0011`): `prices`, `price_days`; `app/prices/` (model, bse, store,
  derived: actions, adjusted closes, returns, volatility, P/E, yield, `snapshot`),
  `app/price_jobs.py` (`sync_prices`, timer), `GET /api/v1/stocks/{symbol}/prices`, the Home
  price chart and watchlist figures, the stock page's Share price card, price-based matching,
  and chat price answers (the latest close as an `F#` cited to that day's BSE file).
- **Not built yet:** P16 hardening. GL is built offline and waits for the owner's apply. Everything after P11 follows [docs/roadmap.md](docs/roadmap.md).
- **Known limitations:** the citation validator proves IDs exist and numbers appear in the cited
  evidence, not full semantic entailment. The fact validator proves the quote, label and number
  are on the cited page, not that the model read the right column or entity (about 1 in 10
  accepted filing facts; the chat names another source's differing figure beside it, while the
  stock page shows only the chosen one, with its source as a link icon: the owner, 2026-09-30). Events and fundamentals
  are limited to the supplied documents and the RBI feed. Prices are end of day only (BSE's
  daily file), and BSE keeps only about a month of files, so history grows a day at a time from
  4 Sep 2026 (a year of history takes a year; longer ranges and price momentum wait for it).

### Open items (resolve before the dependent phase)

- **P2 is done** (ADR 010). Nova 2 Lite was checked and kept for extraction (P11, ADR 020) and
  for chat (P12, ADR 021); Nova Pro stays the unused backup. The owner has an AWS CLI
  profile `stock-analyst-cli` (least privilege, `aws login`, no keys) with two inline policies on
  the group `StockAnalystCli`.
- **Done in P7, kept here because it is easy to forget:** the **one** Google OAuth client now carries
  three redirect URIs (localhost 8000, localhost 3000, and the CloudFront URL). A second client was
  judged unnecessary because the persistent edge means the domain never changes. Production
  `GOOGLE_CLIENT_SECRET` and `SESSION_SECRET` still come from SSM, never the local values.
- **From P7, still open:** re-pointing the edge at a **rebuilt** load balancer is unproven -- the next
  spin-up is the test. `minimum_protocol_version` is stuck at `TLSv1` because the default
  `*.cloudfront.net` certificate forces it; raising it needs a custom domain and an ACM certificate in
  us-east-1. (Re-pointing the edge at a rebuilt load balancer was proven on 2026-09-23.)
- **Final hardening phase (owner's rule: polish waits until the end):** pin base images by digest;
  image signing; Playwright E2E; the auth hardening listed under P16 below; ESLint 10.
- **GL, built offline 2026-10-06 (ADR 008 amendment), not applied:** `S3BlobStore` +
  `BLOB_BUCKET` (`app/blobs.py`), `infra/stack/documents.tf`, the worker container in the api
  task (not essential, restart policy, 1.5 GB cap), task 0.5 vCPU / 2 GB, task-role Bedrock
  (Titan V2 + Nova 2 Lite global profile, ADR 010 shape) and S3 policies, variables
  `data_sources_enabled` / `ai_enabled` / `extraction_budget_usd` / `chat_budget_usd` (both
  containers get the same switches), the deploy script updates every container's image. Each
  session starts with an empty database (no snapshot): the worker refills it in about 20 to 30
  minutes for about $0.56 of Bedrock. Unproven until the first real session: Bedrock accepting
  the global-profile policy, 2 GB being enough, the worker reaching BSE and screener.in from
  AWS addresses (runbook step 6b).
- **P16:** browser E2E (Playwright), button labelling (`aria-pressed` plus a changing label), ESLint 10
  once the Next config supports it (ADR 013).
- **P16 (auth hardening, deferred from P4):** JWKS stale-refresh cooldown (while Google's key endpoint
  is down and the cache is past its TTL, each login retries the fetch and can wait up to the timeout);
  rate limiting on the login endpoints; CSP. All recorded in ADR 012.
- The brief stays untracked on purpose.

## How to run

All backend commands run from `backend/` using the in-folder venv. Keep caches inside the project:
`export UV_CACHE_DIR="$PWD/.uv-cache" PIP_NO_CACHE_DIR=1 UV_PYTHON_DOWNLOADS=never` (Git Bash).

```
# one-time: python -m venv .venv && .venv/Scripts/python -m pip install uv && .venv/Scripts/uv sync
.venv/Scripts/python -m uvicorn app.main:create_app --factory --app-dir src --reload   # run API
.venv/Scripts/python -m pytest --cov                                                    # tests (80% gate)
.venv/Scripts/python -m pytest -m "not integration"                                     # fast tests only
.venv/Scripts/ruff check src tests migrations && .venv/Scripts/ruff format --check src tests migrations
.venv/Scripts/mypy src tests migrations                                                 # types (strict)
.venv/Scripts/alembic upgrade head        # migrate (needs MIGRATION_DATABASE_URL, see below)
.venv/Scripts/alembic history             # list revisions; `alembic current`, `alembic downgrade base`
```
**Database (from the repository root):** copy `.env.example` to `.env` (git-ignored) and set
`POSTGRES_PASSWORD`, `APP_DB_PASSWORD` and `DATABASE_URL`, start Docker Desktop, then
`docker compose up -d --wait` (starts PostgreSQL 16 + pgvector on `127.0.0.1:${DB_PORT:-5432}`),
`docker compose down` to stop, `docker compose down -v` to also delete the data (the init script,
`docker/postgres-init/`, then re-runs). Integration tests need this database and **fail, never skip,**
without it; each integration session first rebuilds the test database from the migrations. The API needs
`DATABASE_URL` (the runtime role) at startup.

**Migrations** use the privileged role, supplied as `MIGRATION_DATABASE_URL`: export it in your shell, or
put only that line in the separate git-ignored `.env.migration` at the repo root (the environment wins).
It never goes in `.env` and is not part of `Settings`. Production migrations are forward-only; `downgrade`
is for local and test verification.

**Google sign-in (local):** `.env` also needs `PUBLIC_BASE_URL`, `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET` and `SESSION_SECRET` (32+ characters); the API refuses to start without them.
Register the redirect URI `<PUBLIC_BASE_URL>/api/v1/auth/google/callback` on your Google OAuth client
(type Web application; scopes `openid email` only). Then open `/api/v1/auth/google/login` in a browser,
and `/api/v1/me` shows who you are. Log out with `POST /api/v1/auth/logout`.

**Frontend (from `frontend/`; Node 22.12+):** keep the npm cache in the project:
`export npm_config_cache="$PWD/.npm-cache"`.

```
npm ci                    # one-time install from the lockfile
npm run dev               # http://localhost:3000, forwards /api/* to the backend (dev-only rewrite)
npm test                  # Vitest (npm run test:coverage enforces 80%)
npm run lint && npm run typecheck
npm run build             # the real production static export, written to out/
```
**Local full-stack flow:** start the database and the backend (port 8000) as above, then `npm run dev` and
open `http://localhost:3000`. For this the backend's `.env` needs `PUBLIC_BASE_URL=http://localhost:3000`,
and `http://localhost:3000/api/v1/auth/google/callback` must be a registered redirect URI on the Google
OAuth client (ADR 013). The browser sees one origin, so there is no CORS. `BACKEND_ORIGIN` (default
`http://127.0.0.1:8000`) is the only frontend variable and is dev-only.

**App stack in containers (repository root; Docker Desktop running):**

```
docker compose --profile app up --build --wait   # db -> migrate -> api -> web; then http://localhost:3000
docker compose --profile app ps -a               # state (migrate shows exited (0), that is correct)
docker compose --profile app logs api --tail 20  # why the api did not start
docker compose --profile app down                # stop, keep the data (never add -v unless you mean to
                                                 # delete the dev database)
```
`.env` needs the database passwords and the Google and session settings above; Compose derives the
container database URLs and `PUBLIC_BASE_URL` (`http://localhost:${WEB_PORT:-3000}`) itself, and ignores
the host values for them. The web container uses the same port as `npm run dev`, so run only one of them;
the api is not published on 8000. If nginx returns 502 after only the api container was recreated,
`docker compose --profile app restart web`. Changing `WEB_PORT` means registering the new redirect URI on
the Google client. Plain `docker compose up -d --wait` stays database-only.

**Container tests (repository root):** `backend\.venv\Scripts\python -m pytest docker/tests` (101 items, about
3 to 4 minutes, Docker Desktop must be running). They use an isolated Compose project and fake credentials
and never touch the dev stack; see `docker/tests/README.md` for cleanup after a killed run.

Health: `GET http://localhost:8000/api/healthz` (liveness) and `/api/readyz` (checks the database).
**The worker** runs in the Compose `app` profile (it restarts itself after a crash). It fetches
filings only with `FILINGS_DISCOVERY=true`, and makes fingerprints and serves search only with
`EMBEDDINGS_ENABLED=true` plus a **temporary AWS pass** from the least-privilege profile
(PowerShell, one window):

```
aws login --profile stock-analyst-cli
aws configure export-credentials --profile stock-analyst-cli --format powershell | Invoke-Expression
$env:EMBEDDINGS_ENABLED = 'true'; $env:FILINGS_DISCOVERY = 'true'
docker compose --profile app up --build --wait
docker compose --profile app restart web
```
The pass lasts about 15 minutes; refresh it with the export line and
`docker compose --profile app up -d --wait api worker`. Never use `stock-analyst-admin` here.
Rebuilding the api or worker from a shell without the pass drops it (search goes off); refresh the
same way. Watch with `docker compose --profile app logs worker --tail 20`; spend so far is
`SELECT sum(input_tokens) FROM embeddings`.

**Chat (P12)** needs the same pass plus `$env:CHAT_ENABLED = 'true'` (optionally
`$env:CHAT_BUDGET_USD = '0.05'`; default cap $1), then the same `up` and `restart web`.

**Prices (ADR 025)** need no AWS pass: `$env:PRICES_ENABLED = 'true'` before the `up`; the worker
then fetches the last month of daily prices (BSE keeps only about a month; 8 files 30 s apart
per run, a 20-minute cool-down after any 406) and one new file each trading day; the stored
history grows from there. Watch with `SELECT count(*) FROM prices`.

**Facts and events (P11)** need the same pass plus `$env:EXTRACTION_ENABLED = 'true'` (optionally
`$env:EXTRACTION_BUDGET_USD = '0.01'` for a one-cent trial; default cap $2), then the same `up`.
After tightening a validator rule, re-check the stored facts for free (no LLM):
`docker compose --profile app run --rm --no-deps worker python -m app.recheck_facts`.

**Terraform (five roots, all run from their own directory).** Offline checks need no AWS:
`terraform init -backend=false`, `terraform fmt -check -recursive .`, `terraform validate`,
`terraform test` (mock providers). The Node test for the CloudFront Function is
`node --test infra/edge/functions/rewrite-index.test.mjs` (name the file; a directory argument does not
resolve on Node 24). Repository-level checks: `backend\.venv\Scripts\python -m pytest infra/tests`.

```
infra/bootstrap   state bucket           applied once, never destroyed
infra/preflight   credential check       read-only, creates nothing
infra/edge        CloudFront + S3 site   applied once, never destroyed, no hourly cost
infra/cicd        ECR + GitHub OIDC      applied once (after the edge), never destroyed, a few cents a month
infra/stack       the application        destroyed after every session ($1.41/day idle, ~$2.30 running)
```

**Do not run `terraform apply` or `destroy` on the owner's behalf** (the Claude Code harness refuses
both anyway). Give the owner the exact command with a pre-apply brief. **In PowerShell, quote any flag
containing `=`** -- `terraform init "-backend-config=backend.hcl"` -- or PowerShell splits the argument
and Terraform reports "No positional arguments are expected".

Switching the demo on and off: the owner runs `scripts\demo-up.ps1` and `scripts\demo-down.ps1`
(every apply/destroy waits for a typed `yes`; no plan file is written). Real timings and the failure
table: **[docs/runbook.md](docs/runbook.md)**. The public URL is permanent and registered with Google.
Offline Terraform in `infra/stack` or `infra/cicd` (whose `.terraform` points at the real backend):
set `TF_DATA_DIR=.terraform/offline` first. Run ruff with `--no-cache`: a stale cache once hid errors.

## Updating CLAUDE.md

Update when a major decision changes, a major component is added, the repo structure changes
significantly, conventions change, a milestone completes, or an important limitation is found.
Keep it short and accurate; do not paste source code or conversation history.
