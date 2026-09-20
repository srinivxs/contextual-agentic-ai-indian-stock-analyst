# 012 — Authentication and sessions

- **Status:** Accepted
- **Date:** 2026-09-21

## Context

The product needs to know who is asking (P5 onward: follows, documents, chat and memory are all
per-user). The MVP has one sign-in method, Google, and no password of our own. P4 was built in five
commits: P4a (`f92f703`, sessions, `/me`, logout), P4b-1 (`bd6974b`, PKCE, signed login state,
authorization URL), P4b-2 (`ae9761d`, JWKS cache and ID-token verification), P4b-3 (`c099a66`, the
login and callback routes) and P4c (this ADR and the real-Google check). This ADR records what was
decided and, in section 6, exactly which test proves each security guarantee.

## Decision

### 1. The flow (OpenID Connect, authorization-code flow with PKCE)

1. `GET /api/v1/auth/google/login` creates one **login attempt**: a random `state`, a random `nonce`,
   and a random PKCE `code_verifier`. The attempt is signed and put in the `oauth_login` cookie. The
   browser is redirected to Google with `state`, `nonce`, the PKCE *challenge* (SHA-256 of the
   verifier, `S256`), `scope=openid email` and `prompt=select_account`. No database, no network.
2. The user signs in at Google. Google redirects back to `/api/v1/auth/google/callback` with a
   one-time `code` and our `state`.
3. The callback makes every check that needs no Google request, **in this order**: Google's `error`
   parameter → the signed `oauth_login` cookie (untampered, not expired) → `state` equals the state
   inside the cookie (constant-time comparison) → a non-empty `code`. A forged or stale callback
   stops here and never causes an outbound request.
4. Only then does the server post the `code`, the client secret and the `code_verifier` (read from
   our own cookie; the browser never sees it in a URL) to Google's token endpoint, over a server-to-
   server call with a timeout. From the response we keep **only the ID token**. The access and
   refresh tokens are discarded.
5. The ID token is verified: RS256 signature against Google's published keys (JWKS), then `iss`,
   `aud` (our client ID), `exp`, `nonce` (equals ours), `email_verified`, and the presence of `sub`
   and `email`.
6. **Only now** is the database opened, in one short transaction that makes no network call: upsert
   the user by Google `sub`, delete expired sessions, create a fresh session. All three or none.
7. The browser is redirected to `PUBLIC_BASE_URL` with the session cookie set and the login cookie
   cleared. Any failure earlier redirects to `PUBLIC_BASE_URL?login_error=login_failed` (or
   `cancelled` when the user pressed cancel at Google) and clears the login cookie.

What each value defends against:

| Value | Defends against |
|---|---|
| `state` (in the signed cookie, compared with the callback's) | **Login CSRF**: an attacker sending a victim a callback that carries the attacker's own code, which would sign the victim into the attacker's account |
| `nonce` (echoed inside the signed ID token) | An ID token issued for a different login attempt being replayed into this one |
| PKCE `code_verifier` | An intercepted authorization code being exchanged by anyone who does not hold the verifier (the code alone is not enough) |
| Client secret | Anyone but our server exchanging a code. This is a *confidential* client, so the secret authenticates it. PKCE adds a second, independent binding; neither replaces the other |

### 2. Server-side sessions, not JWT sessions

- A session is a random 256-bit token (`secrets.token_urlsafe(32)`). The **database stores only its
  SHA-256 hash** (`bytea`, CHECK length 32, unique). A copy of the database (backup, read-only SQL
  injection) cannot be turned into working cookies. A fast hash is sufficient because the token is
  random, not human-chosen: there is nothing to brute-force.
- **Fixed 7-day expiry** (`SESSION_LIFETIME_HOURS`, default 168), set at creation from the database
  clock and never extended by use. There is no `last_seen_at` column. The expiry is checked in the SQL
  lookup itself, so an expired row can never be returned.
- Logout deletes the row, so a stolen cookie stops working immediately. A JWT session could not be
  revoked before its expiry without a server-side denylist, which is a session table by another name.
- Identity is Google's `sub`, never the email: an address can change or be reassigned. `users` keeps
  `google_sub`, `email` (not unique, refreshed on every login) and two timestamps. No name, picture or
  tokens.

### 3. Cookies

| Cookie | Local | Production | Notes |
|---|---|---|---|
| Session | `session` | `__Host-session` | HttpOnly, SameSite=Lax, `Path=/`, no `Domain`; `Secure` from `COOKIE_SECURE`; `Max-Age` = session lifetime |
| `oauth_login` | `oauth_login` | `oauth_login` | Signed (itsdangerous, own salt) attempt, HttpOnly, SameSite=Lax, `Path=/api/v1/auth/google`, `Max-Age` 600 s; `Secure` in production. **Cannot use `__Host-`** because that prefix requires `Path=/` |

- `__Host-` makes a browser accept the cookie only if it is `Secure`, `Path=/` and has no `Domain`, so
  a sibling subdomain cannot overwrite it. Plain-HTTP localhost cannot store a Secure cookie, so
  development uses the plain name. The production settings validator refuses to start unless
  `COOKIE_SECURE` is true and `PUBLIC_BASE_URL` is https.
- SameSite=Lax, never Strict: the callback is a top-level navigation *from* google.com, and a Strict
  cookie would not be sent on it.

### 4. CSRF, and what is not cached

- CSRF defence is SameSite=Lax **plus** an `Origin` check on state-changing methods **plus** logout
  being POST-only. A request whose `Origin` differs from the configured origin, including the literal
  `null`, gets 403 before anything else happens. A request with **no** `Origin` (curl, tests) is
  allowed: a browser always sends one on a cross-site POST. The allowed origin comes from
  `PUBLIC_BASE_URL`, never from `Host` or `X-Forwarded-*` headers.
- `Cache-Control: no-store` is added to `/api/v1/me` and `/api/v1/auth/*` only (including error
  responses), not as a blanket policy.
- Every 401 is the same envelope: unknown, altered, expired and missing cookies are indistinguishable.

### 5. ID-token verification and the Google key cache

- OIDC allows a client to skip signature verification when the token arrives straight from the token
  endpoint over TLS. We verify anyway (owner decision): it costs little and keeps the token safe to
  trust if it ever reaches us by another route.
- PyJWT with the algorithm pinned to **RS256**. The key is chosen by the token's `kid`. Both issuer
  spellings Google uses are accepted. `exp` gets 60 s of clock-skew leeway. `iat` is validated only for
  plausibility (rejected if more than 60 s in the future) and there is **no maximum token age**, so a
  slow login does not fail spuriously.
- **JWKS cache** (`app/auth/jwks.py`, our own, about a page): TTL from Google's `Cache-Control:
  max-age` minus `Age`, clamped to 5 min to 24 h (1 h if unusable); one `asyncio.Lock` so a burst of
  logins on a cold cache fetches once; an unknown `kid` forces one refresh, then a 60 s cooldown; if
  Google is unreachable a cache up to 24 h past its TTL is still used; beyond that, or with no cache,
  or if the needed `kid` is missing, verification **fails closed**. It never accepts an unverified token.
- Alternatives rejected: Authlib or another OIDC client library (more magic than we can explain in an
  interview, for one provider and one flow); Amazon Cognito (an extra AWS service and cost for a
  single-provider login); Redis for sessions (forbidden by the project constraints, and PostgreSQL
  already holds the sessions).

### 6. Invariant → test map

Only what the tests actually prove is claimed. "Unit" tests are pure logic, "API" tests run the app
in-process against a fake Google, "integration" tests use a real PostgreSQL. Paths are under
`backend/tests/`.

| Guarantee | Enforced in | Proven by |
|---|---|---|
| A mismatched `state` is refused and **no request is made to Google** | `api/auth.py` `_verified_exchange` | `api/test_auth_callback.py::test_a_mismatched_state_is_refused_without_ever_contacting_google` |
| Missing, tampered or expired login cookie, or missing `code`, is refused before Google is contacted | same | same file: `test_a_callback_with_no_login_cookie_is_refused_without_contacting_google`, `test_a_tampered_login_cookie_is_refused_without_contacting_google`, `test_a_callback_with_no_code_is_refused_without_contacting_google`, `test_an_expired_login_attempt_is_refused`; `unit/test_login_state.py::test_a_tampered_value_is_refused`, `test_a_forged_payload_with_an_attackers_own_state_is_refused`, `test_a_value_signed_with_another_secret_is_refused`, `test_a_value_older_than_the_configured_ttl_is_refused` |
| Nothing touches the database before the ID token is verified, and a failed login leaves no user and no session | `_sign_in` runs only after `verify_id_token` | `api/test_auth_callback.py` (every failure runs with a database factory that raises if used); `integration/test_auth_login_db.py::test_a_failed_login_leaves_no_user_and_no_session` (exchange refused, bad signature, bad nonce, unverified email, Google down) |
| The PKCE verifier comes from our own cookie and appears in no URL | `auth/google.py`, `auth/login_state.py` | `integration/test_auth_login_db.py::test_the_exchange_carried_the_verifier_from_our_own_cookie`; `api/test_auth_login_route.py::test_the_verifier_stays_in_the_cookie_and_never_reaches_the_url`; `unit/test_google_urls.py::test_the_verifier_itself_is_never_in_the_url`; `unit/test_token_exchange.py::test_the_verifier_is_sent_here_and_only_here` |
| RFC 7636 challenge is correct, `S256` only | `auth/pkce.py` | `unit/test_pkce.py::test_the_challenge_matches_the_rfc_7636_worked_example` |
| ID-token signature, algorithm, issuer, audience, expiry, nonce, verified email, subject and email are enforced | `auth/id_token.py` | `unit/test_id_token.py`: wrong key, unsigned, public-key-as-HMAC-secret, tampered payload, other issuer, other audience, expired, wrong or missing nonce, unverified email, missing subject or email, plus `test_our_own_algorithm_pinning_holds_even_without_pyjwts_safety_net` |
| Identity is `sub`, not email | `auth/users.py`, `auth/id_token.py` | `unit/test_id_token.py::test_the_identity_is_the_google_sub_not_the_email`; `integration/test_auth_login_db.py::test_a_changed_google_email_updates_the_row_without_changing_the_user`; `integration/test_auth_migration.py::test_google_sub_is_the_identity_and_email_is_not_unique` |
| Simultaneous first logins create one user | `ON CONFLICT (google_sub) DO UPDATE` | `integration/test_auth_login_db.py::test_simultaneous_first_logins_for_one_account_create_exactly_one_user` |
| Verification fails closed when Google's keys cannot be obtained; a cached key is used through an outage only within the grace period | `auth/jwks.py` | `unit/test_id_token.py::test_verification_fails_when_google_is_down_and_we_hold_no_key`, `test_verification_succeeds_when_google_is_down_but_the_cached_key_fits`; `unit/test_jwks_cache.py::test_a_cache_past_the_grace_period_is_refused_even_though_the_kid_matches`, `test_the_grace_boundary_is_where_we_say_it_is`, `test_a_stale_cache_that_lacks_the_wanted_kid_fails_rather_than_offering_another_key` |
| One fetch for a burst of logins; unknown-`kid` refreshes are rate-limited | `auth/jwks.py` | `unit/test_jwks_cache.py::test_twenty_simultaneous_lookups_on_a_cold_cache_fetch_once`, `test_a_flood_of_unknown_kids_refreshes_only_once_per_cooldown` |
| Google's access and refresh tokens are discarded; the schema has nowhere to keep tokens | `auth/google.py` `exchange_code` | `unit/test_token_exchange.py::test_googles_access_and_refresh_tokens_are_discarded`; `integration/test_auth_migration.py::test_users_has_the_agreed_columns_and_only_those`, `test_sessions_has_the_agreed_columns_and_only_those` |
| No open redirect, on failure or on success | callback builds its target from configuration only | `api/test_auth_callback.py::test_the_callback_has_no_user_controlled_redirect_target`; `integration/test_auth_login_db.py::test_a_SUCCESSFUL_login_still_ignores_any_redirect_the_caller_suggests` |
| Every failure looks identical to the browser | `_failed` | `api/test_auth_callback.py::test_every_failure_looks_the_same_to_the_browser`; cancel is the one deliberate difference: `test_a_cancelled_login_says_so_rather_than_looking_like_a_failure` |
| A slow Google cannot hang a login | timeout on the shared `httpx` client | `api/test_auth_callback.py::test_a_slow_google_ends_the_login_rather_than_hanging_it` |
| No code, state, nonce, verifier, cookie, token, client secret or session hash is logged | fixed reason labels only | `api/test_auth_callback.py::test_nothing_sensitive_reaches_the_logs`; `unit/test_id_token.py::test_no_log_line_ever_contains_the_token_or_its_claims`; `integration/test_auth_api_db.py::test_no_log_line_ever_contains_the_session_token_or_its_hash`; `unit/test_token_exchange.py::test_the_failure_reveals_nothing_about_the_code_or_our_secret` |
| Only the hash of a session token is stored; tokens are 256 random bits | `auth/sessions.py` | `integration/test_auth_sessions_db.py::test_only_the_hash_is_stored_never_the_token`; `unit/test_session_tokens.py` (256 bits, OS random source, SHA-256 digest); `integration/test_auth_migration.py::test_a_token_hash_must_be_32_bytes_and_unique` |
| The expiry is fixed and enforced; use does not extend it | SQL lookup | `integration/test_auth_sessions_db.py::test_the_lifetime_is_fixed_at_creation`, `test_an_expired_session_finds_nobody`; `integration/test_auth_api_db.py::test_me_does_not_extend_the_session` |
| Expired sessions are cleared out at login without touching live ones | `purge_expired_sessions` | `integration/test_auth_login_db.py::test_logging_in_clears_out_sessions_that_have_already_expired` |
| `/me` returns 401 for every kind of bad cookie, with one envelope | `auth/deps.py` | `api/test_auth_no_db.py` (no or empty cookie); `integration/test_auth_api_db.py::test_me_is_401_for_every_kind_of_bad_cookie` |
| Logout ends only the caller's session; twice is fine; simultaneous logouts succeed | `delete_session` | `integration/test_auth_api_db.py::test_logout_ends_the_session_and_clears_the_cookie`, `test_logging_out_twice_is_fine`, `test_logout_ends_only_the_session_that_asked`, `test_simultaneous_logouts_all_succeed` |
| Logout is POST-only and refuses a foreign origin (including `null`) before touching the session or the cookie | `require_same_origin` | `api/test_auth_no_db.py::test_logout_must_be_a_post`, `test_logout_from_a_foreign_origin_is_refused_before_anything_else_happens`; `api/test_origin_check.py`; `integration/test_auth_api_db.py::test_a_foreign_origin_cannot_log_someone_out` |
| Cookie attributes; `__Host-` rules in production; development and production read only their own cookie name | `auth/cookies.py` | `unit/test_cookies.py`; `api/test_auth_login_route.py` (login-cookie flags, scope, production `Secure`); `integration/test_auth_api_db.py::test_development_reads_only_the_plain_cookie_name`, `test_production_reads_only_the_host_prefixed_cookie_name` |
| Production refuses to start with insecure cookies or a non-https origin; all Google settings are required at startup; secrets never appear in `repr` or validation errors | `core/config.py` | `unit/test_auth_settings.py`, `unit/test_google_settings.py` |
| `no-store` only on authentication responses | `api/middleware.py` | `api/test_no_store_scope.py` |
| The runtime database role can use the tables but not change them | ADR 011 roles | `integration/test_auth_migration.py::test_the_runtime_role_can_use_the_tables_but_not_change_them` |

**How the tests themselves were tested.** After each phase the security-relevant code was
deliberately broken, one change at a time, and the suite had to fail (state check removed or moved
after the Google call, database writes before token verification, a failed login still creating rows,
identity looked up by email, tokens persisted, sensitive values logged, an open redirect, the
`__Host-` prefix on the login cookie). Sources were restored byte for byte afterwards. This found real
gaps, all closed: a stale cache with a missing `kid` (P4b-2, found by coverage), no test that the
algorithm pin holds without PyJWT's own safety net (P4b-2), and, in P4b-3, no test that the
**successful** path ignores a hostile redirect (the open-redirect mutation survived until
`test_a_SUCCESSFUL_login_still_ignores_any_redirect_the_caller_suggests` was added). Of the 18 P4b-3
breakages, 16 were caught outright; the state-check removal alone was redundant with the cookie check
and was caught in combination.

### 7. Verified against the real Google

On 2026-09-21 the owner ran the flow locally (`http://localhost:8000`, a Google OAuth client in
testing mode, real Google account, real Postgres) and reported only status codes, redirect targets and
row counts, never a cookie, code or token.

| Check | Result |
|---|---|
| `GET /api/v1/auth/google/login` | 302 to `https://accounts.google.com/o/oauth2/v2/auth`; `oauth_login` cookie `HttpOnly; Max-Age=600; Path=/api/v1/auth/google; SameSite=lax`; `Cache-Control: no-store` |
| Real callback, user allows | Login completed and redirected to `PUBLIC_BASE_URL`, which is a JSON 404 until the frontend exists (P5) |
| `GET /api/v1/me` | 200 with the signed-in user's `id` and `email` |
| Database | One `users` row; each `sessions` row has a 32-byte `token_hash` and a 7-day lifetime. Two sessions existed after the second login of the day, because a new login never revokes an earlier session |
| `POST /api/v1/auth/logout` from the browser (same origin) | 204 |
| `GET /api/v1/me` afterwards | 401 `unauthorized` envelope |
| Database after logout | Sessions went from 2 to 1: logout removed only the caller's session. The user row stayed |
| Cancelled login (Cancel pressed on Google's real consent screen, after the app's access was revoked) | Browser ended at `http://localhost:8000/?login_error=cancelled`; the database was unchanged (1 user, 1 session, no new rows). A hand-made callback with a wrong `state` and no cookie returns `?login_error=login_failed` on the same running app |

The redirect target in the address bar after the successful callback was not read back separately;
that no redirect parameter can alter it is proven by the two open-redirect tests in section 6.

## Consequences

**Good**
- Nothing sensitive lives anywhere it does not have to: no passwords, no tokens stored, no Google
  refresh token, session tokens only as hashes.
- Every failure path is cheap and uninformative to an attacker, and every guarantee has a named test.
- The whole login is a handful of small modules that each fit on a screen.

**Known limitations (accepted for the MVP)**
- **Replay of the login cookie is stopped by Google, not by us.** The signed `oauth_login` cookie is
  stateless and would verify again until its 10-minute `Max-Age`/signature age runs out; the browser is
  told to delete it after use, but a copy would still pass our own checks. A second use fails because
  Google refuses to redeem a code twice (`test_the_same_login_cookie_cannot_be_used_twice` simulates
  that refusal). We deliberately keep no server-side attempt table.
- **No rate limiting** on the login endpoints (excluded from P4). A flood can cause requests to Google
  and database work; a hardening item for P16.
- **No CSP** and no "log out everywhere" or session list.
- **Deferred: JWKS stale-refresh cooldown.** While Google's key endpoint is down and our cache is past
  its TTL, every login retries the fetch, so each login can wait up to the timeout (5 s default)
  before falling back to the stale cache. The unknown-`kid` refresh already has a cooldown; a cooldown
  for stale-cache refreshes was left out to keep P4b closed. Revisit in P16.
- **Expired sessions are only purged when someone logs in.** They can never be used (the lookup checks
  expiry), but rows accumulate between logins.
- **A database failure at the callback returns the JSON 500 envelope**, not the friendly
  `login_error` redirect. The user simply starts over.
- **Fixed 7-day sessions**: users sign in again weekly, by design.
- **JWKS cache is per process**; the `api` and `worker` containers do not share one. Only `api`
  verifies tokens.
- The stored email is whatever Google reported at the last login and is not unique.

**Handoffs to later phases**
- **P5:** the frontend must show a fixed message per `login_error` value and treat any other value as
  the generic one; never render the query value.
- **P7 (Terraform):** the CloudFront behaviour for `/api/*` needs caching disabled and cookies, query
  strings and the `Origin` header forwarded. `Secure` correctness depends on configuration
  (`COOKIE_SECURE`, https `PUBLIC_BASE_URL`), not on what the ALB hop looks like.
- **P8 (deploy):** a second Google OAuth client whose redirect URI is the CloudFront URL, and
  `GOOGLE_CLIENT_SECRET` and `SESSION_SECRET` from SSM Parameter Store, never the local values.
  (The CI trigger question is tracked in `the project notes`.)
