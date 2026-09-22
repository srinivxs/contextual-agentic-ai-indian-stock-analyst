# 015 — A persistent edge and an ephemeral application

- **Status:** Accepted (amends [ADR 008](008-minimal-aws-architecture.md), which said the whole AWS
  stack is destroyed and recreated at will; see Amendments there. Builds on
  [ADR 004](004-ecs-fargate.md), [ADR 006](006-nextjs-static-export.md),
  [ADR 011](011-database-access-and-migrations.md) and [ADR 012](012-authentication-and-sessions.md))
- **Date:** 2026-09-22

## Context

P7 built the AWS stack as one root module that `terraform destroy` removes completely, because the
project is demonstrated occasionally and must cost nothing in between (ADR 008). P7e was to add the
last piece: CloudFront and the S3 static site, so the application is served over HTTPS from one origin.

Designing it surfaced a problem the earlier phases could not have seen.

**A CloudFront distribution is given a new `*.cloudfront.net` domain every time it is created.** That
domain is not cosmetic here. It is:

- the `redirect_uri` registered on the Google OAuth client, which must match exactly or sign-in fails
  with `redirect_uri_mismatch`;
- `PUBLIC_BASE_URL`, which the backend uses to build that redirect URI and to check the `Origin`
  header (ADR 012);
- the origin the browser sees, which is what makes the session cookie first-party and lets the whole
  design work without CORS (ADR 013).

If the distribution died with everything else each night, every cold start would need a Google console
change. Google's own documentation warns that a redirect-URI change can take from five minutes to
several hours to take effect. Thirty minutes before an interview, that is not a risk worth carrying for
a saving that turns out not to exist.

## Decision

### 1. Split the deployment by LIFETIME, not by layer

| Root | Lifetime | Contents |
|---|---|---|
| `infra/bootstrap` | forever | the Terraform state bucket (unchanged since P7a) |
| **`infra/edge`** | **forever** | S3 site bucket, CloudFront distribution and Function, the shared origin secret |
| `infra/stack` | destroyed after every session | VPC, ALB, ECS, RDS, ECR, IAM, logs, application secrets |

The line is drawn where the money is. **Nothing in `infra/edge` has an hourly or monthly rate.**
Verified against the AWS Price List API on 2026-09-22: CloudFront has no "Fee" product family at all,
so an idle distribution costs nothing; charges are per request ($0.012 per 10,000 HTTPS in India) and
per GB out ($0.120/GB for the first 10 TB). Add a few hundred kilobytes of static files at $0.025/GB
per month and two standard SSM parameters, which are free.

**The persistent half costs under a cent a month. The ephemeral half costs $1.41 a day idle and $1.84
a day running.** Keeping the domain alive is therefore free, and it removes the only step of a cold
start that depends on a third party's propagation delay.

### 2. How the two roots exchange values

The rule, and the reason for it:

```
persistent ──▶ ephemeral      through SSM parameters
ephemeral  ──▶ persistent     through a command-line variable
```

`infra/edge` writes two parameters and never deletes them:

| Parameter | Type | Read by |
|---|---|---|
| `/stock-analyst/demo/origin_verify` | SecureString | the ALB listener rule |
| `/stock-analyst/demo/public_base_url` | String | the api container's `PUBLIC_BASE_URL` |

`infra/stack` reads both with data sources, collected in one file — `infra/stack/edge.tf` — so the
dependency between the roots is visible in a directory listing rather than buried in whichever
resource happens to use it.

**A data source must never point at something that is destroyed nightly.** That is why the one value
travelling the other way — the load balancer's DNS name, which changes on every rebuild — is passed as
`-var alb_origin_domain=...` after the stack applies, and has a placeholder default
(`origin-not-deployed.invalid`, a reserved TLD) so the edge can be applied first, before any
application exists.

**Consequence, and it is intended:** a plan of `infra/stack` fails if `infra/edge` has never been
applied. The edge is the permanent half; if it is missing, something is wrong and stopping is right.

### 3. The shared origin secret moved to the edge

`random_password.origin_verify` was created by `infra/stack` in P7d. It now lives in `infra/edge`,
because **CloudFront and the ALB listener rule must agree on it across a rebuild**. Had the stack kept
minting its own, every API request after a rebuild would have met the listener's default `403` until
CloudFront was updated too.

It is a stateful resource, not ephemeral: a listener-rule condition and an origin header are ordinary
configuration that Terraform compares on every plan, so the value is in state either way. That is
acceptable because the state is encrypted in S3 and access-controlled, and because this header is
defence in depth behind the security group's CloudFront prefix list, not a primary credential.

### 4. One hostname, two completely different things

```
Browser ── https://<distribution>.cloudfront.net ──▶ CloudFront
                                                        ├─ /api/*  ─▶ ALB ─▶ Fargate api
                                                        └─ everything else ─▶ S3 (private, OAC)
```

Serving both from one hostname is the security design, not a convenience: the browser sees a single
origin, so the session cookie is first-party, the backend's `Origin` check passes, and no CORS headers
exist anywhere (ADR 012, ADR 013).

- **The S3 bucket is private.** Not a website endpoint, no public policy. CloudFront signs every
  request with SigV4 (Origin Access Control), and the bucket policy allows exactly one principal —
  `cloudfront.amazonaws.com` — restricted by `AWS:SourceArn` to this distribution, `s3:GetObject` only,
  no `ListBucket`. Without the `SourceArn` condition any distribution in any AWS account could read it.
- **A CloudFront Function rewrites paths.** The frontend is a static export with `trailingSlash: true`,
  so `/stocks/` is the object `stocks/index.html`. S3 behind OAC is object storage with no directory
  index, so something must turn a request path into an object key. A viewer-request Function does it in
  under a millisecond, with no cold start and no Lambda@Edge. It is attached to the static behaviour
  only: appending `/index.html` to `/api/v1/me` would 404 every call.
- **`/api/*` caching is disabled, and that is a security property.** Every API response is selected by
  the session cookie; a cached `/api/v1/me` would be handed to the next visitor as their own identity.
  The behaviour uses the managed `CachingDisabled` policy with `AllViewerExceptHostHeader`, which
  forwards the cookie, query string and `Origin` while leaving `Host` as the load balancer's own name.
- **There is no `custom_error_response`.** It is distribution-wide and cannot be scoped to one
  behaviour, so mapping 404 to `/404.html` would replace the backend's own 404 JSON — which the API
  returns by design for a resource belonging to someone else — with a page of HTML. A prettier static
  404 is not worth corrupting the API contract; a missing static path shows S3's bare error instead.

### 5. Production mode, at last

With an HTTPS origin, `app_env` defaults to `production`, which switches the session cookie to the
`__Host-` prefix (ADR 012). Two settings must agree with that or the backend refuses to start, so
neither is left to be remembered:

- `PUBLIC_BASE_URL` comes from the edge's parameter, and a `lifecycle.precondition` on the api task
  definition refuses `production` with a non-https URL **at plan time**.
- `COOKIE_SECURE` is **derived**: `tostring(var.app_env == "production")`. A separate variable would be
  one more thing to forget, which is exactly how the first production apply failed (see Verification).

## Consequences

**Good.** The domain is permanent, so the Google client is configured once and never again. The
expensive tier still dies every night. The persistent tier is free. The frontend survives teardown, so
a cold start has one less step.

**The price.** Three roots instead of two, each with its own state file, and **two applies per cold
start** rather than one: the stack first, then the edge with the new ALB name. ADR 008's "the whole
stack is cleanly destroyable" is no longer literally true — it is true of everything that costs money,
which is the property that mattered.

**A trap to avoid.** `infra/edge/backend.hcl` must use `key = "edge/terraform.tfstate"`. The
bootstrap's `backend_config_hcl` output names `stack/`, because it was written for that root. Piping it
in unedited would point two roots at one state file, and each would destroy the other's resources on
its next apply.

## Verification

Applied and torn down for real on 2026-09-22, at a total cost of about $0.10.

| Checked | Result |
|---|---|
| Static site over HTTPS | `/` 200; `http://` redirected 301 |
| Function rewrite in the real runtime | `/stocks` and `/stocks/` both 200; `_next` assets untouched |
| Bucket privacy | the S3 URL directly returns 403 |
| `/api/healthz` through CloudFront | 200 |
| **`/api/readyz`** | **200 `{"status":"ready"}`** — the api queried RDS as `stock_app` |
| Unauthenticated API | `/api/v1/me` and `/api/v1/stocks` return 401 |
| Sign-in start | correct `redirect_uri`, scope `openid email`, `state` and PKCE present, `oauth_login` cookie carrying `Secure` |
| Real Google sign-in | completed by the owner |
| **The session cookie** | **`__Host-session`** with `HttpOnly`, `Secure`, `SameSite` |
| Teardown | 51 destroyed; ephemeral tier empty; CloudFront, both buckets and both parameters intact; `/` still 200 while `/api/healthz` returns 502 |

That last row is the design in two numbers: the domain and the Google registration survive, and only
the expensive half is gone.

**Timings, for the cold-start runbook:** stack apply ~7 minutes (RDS 6m6s, ALB 2m12s); distribution
create 3m35s, but an **origin update only 33 seconds**; stack destroy ~5 minutes.

### Three bugs found here that no offline test caught

Each now has a test.

1. **`command = ["migrate"]`** on the migration task. The image has no `ENTRYPOINT`, so `command`
   replaces the CMD outright and there is no such executable — ADR 014's "migrate" names a Compose
   *service*. The offline test asserted a substring and so inherited the same ambiguity. It now asserts
   the exact argv.
2. **`ALTER ROLE ... NOSUPERUSER`** in the provision task. Legal locally, where the Compose admin
   really is a superuser; refused on RDS, where the master user is `rds_superuser`. The ALTER path now
   sets only the password, and the attributes are read back from `pg_roles` and refused if wrong.
3. **`COOKIE_SECURE` missing** from the api task definition. Nothing had ever run in production mode.
   Beyond deriving the flag, a hygiene test now reads the backend's own `Settings` class and asserts
   that every field it refuses to start without appears in the task definition — so forgetting a
   *future* required setting fails in a test rather than in CloudWatch.

The general lesson, worth stating plainly: **mock providers and a local PostgreSQL cannot substitute
for one real apply.** All three bugs passed every offline gate.

## Known limitations

- **`minimum_protocol_version` is `TLSv1` and cannot be raised.** CloudFront forces it when using the
  default `*.cloudfront.net` certificate, so viewers may negotiate TLS 1.0 or 1.1. Raising it to
  TLS 1.2 requires a custom domain with an ACM certificate in `us-east-1`, which the project notes rules out
  until something proves it necessary. Recorded rather than hidden.
- **Re-pointing the edge at a rebuilt ALB is not yet proven.** The distribution has only ever been
  pointed at its first real origin. That is the demo cold-start path; the next spin-up is the test.
- **Two Terraform assertions in `infra/edge/tests/edge.tftest.hcl` are unenforceable under mocks.**
  Every data source of one type resolves to a single generated value, and `override_data` does not take
  effect, so a swap of the two managed cache policies is invisible. The enforcement lives in text
  assertions in `infra/tests/test_infra_hygiene.py`; a real plan also shows the true distinct UUIDs.
- The frontend is uploaded by hand with `aws s3 sync`. P8 automates it, with a cache invalidation.
