# 013 — The follow API, plain SQL, and the static frontend shell

- **Status:** Accepted (amended by [ADR 014](014-containers-and-local-compose.md), see Amendments)
- **Date:** 2026-09-21

## Context

P5 is the first full-stack slice: a user signs in, follows stocks, refreshes, and the follows are
still there. It adds one table (`user_follows`), three endpoints, and the first frontend. It builds on
ADR 006 (static export), ADR 007 (three stocks), ADR 011 (database access) and ADR 012 (sessions),
none of which is reopened.

## Decision

### 1. Plain SQL, not an ORM (amends the forecast in ADR 011)

ADR 011 said ORM models would arrive with the follow API. They do not. All data access, in P4 and
now, is SQLAlchemy Core `text()` statements with bound parameters, in small functions that never
commit (the caller owns the transaction). One two-column table and three statements do not justify a
second layer of model classes to explain, map and keep in step with the migrations. Revisit if a phase
needs relationship loading or many similar queries. ADR 011 carries an amendment note pointing here.

### 2. The `user_follows` table (migration 0003)

`(user_id uuid, stock_id bigint, created_at timestamptz)`, primary key **(user_id, stock_id)**, no
surrogate id. Deleting a user cascades to their follows; deleting a followed stock is refused, because
the universe is fixed and removing a stock should be a deliberate migration.

### 3. The API (all under `/api/v1`, all need a session)

| Call | Success | Errors |
|---|---|---|
| `GET /stocks` | 200 `{"items":[{symbol,name,bse_code,sector,followed}]}` in seed order | 401 |
| `PUT /stocks/{symbol}/follow` | 204, idempotent | 401, 403, 404, 422 |
| `DELETE /stocks/{symbol}/follow` | 204, idempotent | 401, 403, 404, 422 |

- **Why PUT and DELETE:** they are idempotent. "Make it so that I follow this" repeated ten times is the
  same as once, which is exactly what retries, double clicks and concurrent tabs need. A `POST` would
  invite duplicates or a spurious 409.
- **The check order is fixed: Origin, then session, then symbol validation.** A cross-site page learns
  nothing (not even whether you are signed in) and an anonymous caller learns nothing about which
  symbols are valid. Unknown but well-formed symbols are 404; badly formed ones are 422 (the regex
  `^[A-Z0-9&-]{1,20}$`, so lowercase is rejected, not fixed up). Neither response repeats the input.
- **Ownership comes only from the session.** No route reads a user id from the path, query or body.
- **Idempotency lives in the database.** The follow is `INSERT ... ON CONFLICT (user_id, stock_id) DO
  NOTHING`, so concurrent PUTs converge on one row without an application lock. A repeated PUT does
  not change `created_at`. Unfollow is a plain `DELETE`, and deleting nothing is still 204.
- **Nothing internal leaves the server:** no user id, stock id or `is_financial`.
- **No pagination** on the list: the universe is three stocks (ADR 007). The response is an object with
  `items`, so a field can be added later without breaking callers.
- **`Cache-Control: no-store`** now also covers `/api/v1/stocks` and `/api/v1/stocks/...`, because the
  list carries the caller's own follows. The prefix ends in a slash, so `/api/v1/stocks-archive` and
  similar look-alikes are not caught.

### 4. The frontend (ADR 006 applied)

Next.js App Router, TypeScript, **static export**: `output: 'export'`, `trailingSlash`, unoptimised
images. Two routes: `/` (sign in, or on to the stocks page if already signed in) and `/stocks/` (three
cards with a follow toggle). Runtime dependencies are `next`, `react` and `react-dom` only; plain CSS;
no data-fetching library. The server is the source of truth: after every follow or unfollow the list is
fetched again.

- **One place talks to the backend** (`lib/api.ts`). It only calls paths under `/api/v1/`: absolute,
  protocol-relative, backslash and `..` paths are refused before any request is made. It sends the
  cookie by `credentials: 'same-origin'` and nothing else; the session stays HttpOnly and the browser
  manages it. JavaScript never reads, stores or sets it: no `localStorage`, `sessionStorage` or
  `document.cookie`.
- **Login is a normal browser navigation** (an `<a href="/api/v1/auth/google/login">`), not a fetch,
  so the browser follows the redirects to Google and back itself.
- **Failure is not sign-out.** Only a 401 from `/api/v1/me` means "signed out". A 500 or a dropped
  connection shows an error and does not redirect.
- **`login_error` is untrusted input.** Only `login_failed` and `cancelled` are recognised, through a
  `Map` (an object lookup would find inherited properties such as `constructor`); anything else shows
  the generic message. The raw value is never rendered, and React renders all server text as text.
- **Guards, not conventions.** `frontend/tests/guards.test.ts` fails the build if the source gains a
  route handler, middleware or proxy file, a dynamic segment, a server action, `next/headers`,
  `dangerouslySetInnerHTML`, script-visible storage, `NEXT_PUBLIC_` variables or a hard-coded URL, or
  if the production config stops being a static export.

### 5. Local development: one origin, no CORS

`next dev` on `http://localhost:3000` forwards `/api/*` to the backend on `http://127.0.0.1:8000` with a
**development-only** rewrite in `next.config.mjs`, which is a function of the phase. Production (the
static export) has no rewrites, redirects or headers. The browser therefore sees one origin, as it will
behind CloudFront, so the session cookie and the backend's Origin check work unchanged and **no CORS
headers are added anywhere**.

- `PUBLIC_BASE_URL=http://localhost:3000` for this workflow, and
  `http://localhost:3000/api/v1/auth/google/callback` is registered as an additional redirect URI on the
  local Google OAuth client. The backend still runs on port 8000.
- The one frontend variable is `BACKEND_ORIGIN` (default `http://127.0.0.1:8000`), read only by
  `next.config.mjs` at dev-server start. It is not a `NEXT_PUBLIC_` variable and never reaches the
  browser. The frontend itself always uses relative `/api/v1/...`, so production needs no frontend
  configuration at all.
- In development `skipTrailingSlashRedirect` is on. Otherwise `trailingSlash` would redirect
  `/api/v1/me` to `/api/v1/me/`, which the backend does not have.
- In development `agentRules` is off. Next 16.3's dev server otherwise writes an `AGENTS.md` and a
  `the project notes` into `frontend/`; instructions for AI tools are the owner's decision, not a dependency's.
- The proxy was probed against the running backend without Google: `/api/healthz` passes through, the
  login route's 302 arrives with its `Location` and `Set-Cookie` intact, and a 401 error envelope
  passes through unchanged.

### 6. Toolchain (pinned exactly in `package-lock.json`)

Next 16.3.5, React 19.3.0, **TypeScript 5.9.3**, Vitest 5.0.1 with Testing Library, **ESLint 9.39.5**.
- TypeScript 7 (native) exists on npm but was not tried; Next's build uses the TypeScript JS API, so the
  5.9 line stays until Next documents support for 7.
- ESLint 10.11.0 was tried against `eslint-config-next` 16.3.5 and fails on every file
  (`context.getFilename is not a function` inside the bundled `eslint-plugin-react`), so ESLint stays on
  9.39.5, which npm now marks deprecated. Revisit when the Next config supports ESLint 10.

## Alternatives considered

| Option | Why not |
|---|---|
| SQLAlchemy ORM models for follows | A second layer for one table; see section 1 |
| `POST /follows` with a body | Not idempotent; the user could be sent in the body, which is a whole class of bug |
| Serve the static build from FastAPI locally | Puts a local-only path in production code; the dev-only rewrite keeps it in configuration |
| CORS between :3000 and :8000 | More attack surface and a different cookie story from production, to work around a dev-server detail |
| SWR, React Query, a UI kit or Tailwind | Dependencies with no second use; one fetch helper and plain CSS are enough |

## Consequences and known limits

- **Verified by automated tests:** the follow endpoints and migration against real Postgres (including 20
  simultaneous PUTs and mixed PUT/DELETE), a whole login → follow → rebuild the app → still followed →
  logout → login again flow, the frontend behaviour against a fake backend, and a real production
  `next build` that exports `/` and `/stocks/`.
- **Not testable locally:** the production shape (CloudFront in front of S3 and the ALB). That waits for
  P6 (Docker parity) and P7 (CloudFront rules: caching off for `/api/*`, cookies, query strings and
  `Origin` forwarded).
- The frontend is client-rendered: a visitor sees a short "Loading…" before the sign-in link or the
  stocks appear.
- The toggle labels the button "Follow X" / "Unfollow X" and also sets `aria-pressed`; a screen reader
  may announce both. Acceptable for the MVP; revisit in P16.
- Browser end-to-end tests (Playwright) are deferred to P16.

## Deferred

Stock detail (`?symbol=`), facts, sentiment and citations; documents; chat; memory; matching; the
frontend Dockerfile (P6); the CloudFront function (P7); frontend deployment (P8).

## Verified by hand

On 2026-09-21 the owner ran the full local flow (backend on `127.0.0.1:8000` with migration `0003`,
`next dev` on `http://localhost:3000`, `PUBLIC_BASE_URL=http://localhost:3000`, the extra redirect URI
registered on the local Google client, a real Google account, real Postgres): sign in, follow a stock,
refresh and find it still followed, unfollow, sign out, sign in again, and the `login_error` messages.
The owner reported that every step behaved as described, with no CORS errors. Only that summary is
recorded here; no cookie, token or credential was shared.

## Amendments

### 2026-09-21: the production shape, partly testable now (P6, [ADR 014](014-containers-and-local-compose.md))

Nothing above is rewritten; this updates two statements that were true when they were written.

- **"Not testable locally: the production shape."** Half of it is now tested. The `--profile app` stack
  serves the static export and the API from **one origin** through nginx, and container tests prove that
  the `Origin` header, the session cookie, the login redirect and the backend's JSON errors pass through
  untouched. What is still untestable locally, and waits for P7 and P8: the CloudFront rules
  (caching off for `/api/*`, cookies, query strings and `Origin` forwarded), the CloudFront Function,
  HTTPS and the production-mode cookie (`__Host-session`, `Secure`).
- **"Deferred: the frontend Dockerfile (P6)."** Done in P6.
- **A second local workflow (section 5).** Besides `npm run dev` (with its dev-only rewrite to a backend
  on port 8000), `docker compose --profile app up --build --wait` serves the same app on
  `http://localhost:3000` from containers. They use the same port and cannot run at the same time. With
  the containers the api is not published, and its `PUBLIC_BASE_URL` is derived by Compose from
  `WEB_PORT`, so the redirect URI already registered for port 3000 keeps working.

## Amendment (2026-09-29): the light / dark switch

The owner asked for a neutral look with light and dark modes and a switch in the top bar. The
choice ("light" or "dark", nothing else) is kept in `localStorage` so it survives page loads (a
static export loads each page afresh), and one constant script of our own in the page head
applies it before the page draws (no flash). Both break a guard rule written for secrets and
injected markup, so `tests/guards.test.ts` allows each in exactly one file as one exact text
(`src/lib/theme.ts`, `src/app/layout.tsx`) and still catches any other use. The session never
leaves its HttpOnly cookie.
