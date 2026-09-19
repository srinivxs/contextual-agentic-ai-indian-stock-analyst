# 006 — Next.js as a static export on S3 + CloudFront

- **Status:** Accepted
- **Date:** 2026-09-19

## Context

The owner chose **Next.js** for the frontend (overriding the earlier Vite SPA recommendation).
Constraints from ADR 004: this is a temporary demonstration deployment, so there must be
**no always-on frontend container**, and the browser must reach the SPA and the API on **one
origin** (CloudFront) so session cookies work without CORS.

## Decision

Use **Next.js (App Router, TypeScript)** configured with **`output: 'export'`**, producing plain
static files that are uploaded to S3 and served by CloudFront. Next.js is a build tool here; no
Node server runs in production.

Rules that follow from static export (they must not be broken silently):

- **No** server-side rendering, route handlers (`app/api`), server actions, or Next middleware.
  All data comes from the FastAPI backend via client-side `fetch` to `/api/v1/...`.
- **No dynamic route segments for arbitrary tickers** (`/stocks/[symbol]`), because static export
  needs every path known at build time. Use a static route with a query parameter
  (`/stocks?symbol=TCS`).
- `next/image` runs with `images: { unoptimized: true }` (no image-optimisation server).
- `trailingSlash: true`, so each route exports as `route/index.html`. A CloudFront Function
  rewrites extension-less URLs to that file, scoped to the S3 behaviour only (so API 404s are
  never rewritten to HTML).
- Auth is a same-origin HttpOnly session cookie; the client just calls `/api/v1/me`.
- A frontend Dockerfile exists only for local parity, not for production.

## Alternatives considered

| Option | Why not |
|---|---|
| Vite + React SPA | Simpler and lighter (my original recommendation), but the owner prefers Next.js, and it is a widely recognised skill for interviews. |
| Next.js server (`standalone`) on Fargate | A third always-on container plus more ALB routing and cost, for no feature we need. |
| AWS Amplify Hosting | Another managed service to learn and destroy; less transparent than S3 + CloudFront under Terraform. |

## Reasoning

Static export keeps production to two cheap, stateless AWS pieces (S3 + CloudFront, effectively
free at demo volume) and matches the destroy-and-recreate lifecycle. Every feature we need
(login, follow, chat, citations) is a client-side view over the JSON API.

## Tradeoffs

- We use Next.js mainly as a build system and router; its server features are unavailable.
- Larger toolchain than Vite (slower installs and builds).
- Static-export limits (no dynamic ticker routes) shape URL design.
- If SSR is ever needed, this decision must be revisited together with ADR 004.
