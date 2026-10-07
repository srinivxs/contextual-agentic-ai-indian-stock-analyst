# 017 — The deploy pipeline: migrate first, roll out, check, and fail loudly

- **Status:** Accepted (P8c; the commit that marked it Accepted was the first deploy to a running
  stack, 2026-09-23. Builds on
  [ADR 016](016-cicd-registry-and-github-identity.md), [ADR 015](015-persistent-edge.md) and
  [ADR 005/008](008-minimal-aws-architecture.md))
- **Date:** 2026-09-23

## Decision

After the image job pushes a commit's image, a `deploy` job assumes a **separate deploy role**
(`infra/cicd/deploy.tf`; same trust as the push role, main only) and runs
`.github/scripts/deploy_backend.py`, then publishes the frontend.

1. **Stack down** (cluster missing or service not ACTIVE) → nothing to deploy, the job is green. The
   next `terraform apply` of `infra/stack` runs the newest image anyway (ADR 016, P8b). **Any other
   error fails the job**: "access denied" must never pass for "the stack is down".
2. **New revisions**: the current api and migrate task definitions, with only the image replaced,
   named by digest.
3. **Migrate first**, as a one-off Fargate task on the service's own network, while the old version
   keeps serving. Migrations are expand/contract, so the old code runs on the new schema.
4. **Roll out**: `update-service`, `wait services-stable`, then compare the PRIMARY deployment with the
   requested revision, because a circuit-breaker rollback also ends "stable".
5. **Check** `/api/readyz` through CloudFront (internet → edge → ALB → task → database).
6. **Frontend**: build, `s3 sync --delete`, invalidate `/*`. It runs only after the backend step
   succeeded (new pages may call new endpoints), and also when the stack is down (the edge is permanent).

The deploy role can do only that: `RunTask` on the migrate family in this cluster, `UpdateService` on
the one service, `PassRole` on the three task roles to ECS only, `s3` on the site bucket, one
distribution's invalidation, and four named SSM parameters (never the origin or Google secrets).
`Describe/RegisterTaskDefinition` accept only `*`; registering runs nothing. The site bucket and
distribution id reach `infra/cicd` and the job through two new String parameters published by
`infra/edge`, so **edge is applied before cicd**.

## What happens when something fails

| Failure | Result |
|---|---|
| A check job fails | No image is built; nothing reaches AWS |
| The migration exits non-zero, or never starts | The service is never touched; the old version keeps serving. The job fails and names the log group |
| The new task never becomes healthy | ECS's circuit breaker rolls back; the job fails ("rolled back"). The schema is already migrated, which expand/contract makes safe |
| `/api/readyz` never answers ready | The job fails; the new version is live, so this is the signal to look (or redeploy an older commit with `-var image_tag=<sha>`) |
| The backend step fails | The frontend is not published |

## Tradeoffs

- The migration runs on every deploy (about a minute) even with nothing to migrate; `alembic upgrade
  head` is a no-op then, and one fixed path is easier to reason about than a conditional one.
- Revisions registered by CI outlive `terraform destroy` (they cost nothing). Terraform and CI both
  converge on "the newest image", so a later apply does not fight a deploy.
- A failed migration does not half-apply: `migrations/env.py` runs the whole upgrade in one
  transaction and PostgreSQL DDL is transactional, so the schema stays at the previous revision.
  (A future migration that cannot run in a transaction, such as `CREATE INDEX CONCURRENTLY`, would
  lose that and needs its own note.)
- Verified offline (20 script tests, 9 cicd policy tests, workflow tests, 13 breakages), then live.
- Do not push while `scripts/demo-up.ps1` is running: its migrate task and a deploy's could run at
  the same moment. Transactional DDL means one would fail cleanly, not corrupt anything, but it is noise.

## Amendment (2026-10-08): only what changed

A page change waited about 12 minutes for backend tests, an image and a rollout it did not need.
A first job, `changes`, now compares the commit with the newest **green** run on main
(`.github/scripts/changed_parts.py`, tested in `infra/tests/test_changed_parts.py`):

- only `frontend/` changed: the backend tests, the image and the backend step are skipped; the
  frontend and repository checks still run, then the site is published (a few minutes);
- nothing in `frontend/` changed: the site is not republished (this also stops re-uploading every
  file to S3 on every push);
- anything else, or any doubt (no green run, an unknown base, a re-run): everything, as before.

The base is the last green run, not the previous push, so a backend change whose run failed is
counted again by the next push. A failed check still stops everything after it, and a failed
backend step still stops the site from being published.

