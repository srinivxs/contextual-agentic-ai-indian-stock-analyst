# 008 — Minimal AWS architecture: one task, two containers

- **Status:** Accepted (amends [ADR 004](004-ecs-fargate.md); amended by [ADR 014](014-containers-and-local-compose.md), see Amendments)
- **Date:** 2026-09-20

## Context

ADR 004 chose ECS Fargate and a minimum AWS footprint. After the MVP scope was reduced (three stocks,
about 20 documents, no live market data; see ADR 007), several pieces of that design are more than the
product needs: a separate worker service and an EventBridge schedule. Uploaded documents also need
somewhere to live that is not the database. The owner wants the smallest architecture that is still
real, cheap to run, and trivially destroyed.

## Decision

```
Browser → CloudFront ─┬─ /*      → S3 (Next.js static export)
                      └─ /api/*  → ALB → ECS Fargate: ONE service, ONE task, TWO containers
                                          ├─ api    (FastAPI)
                                          └─ worker (job loop + scheduler tick)
                                                    │
                                         RDS PostgreSQL + pgvector      Bedrock (IAM)
                                                    │
                                         S3 private bucket (uploaded documents)
```

1. **One ECS service, one task definition, two containers from the same image**: `api` and `worker`.
   Both are `essential`, so a crash of either restarts the task (a visible failure rather than a
   silently dead worker). Only `api` sits behind the ALB.
2. **No EventBridge Scheduler.** The worker runs a small in-process timer that enqueues a `poll_feed`
   job per time bucket. The job's dedupe key (ADR 005) makes duplicate ticks harmless, so even several
   worker instances would be safe.
3. **A second, private S3 bucket for uploaded documents**, behind a `BlobStore` interface. Local
   development and tests use a filesystem implementation; only AWS uses S3. The bucket blocks all
   public access; only the task role can read or write it; originals are never served publicly.
4. Migrations still run as a one-off ECS task from the same image before each deploy (ADR 004).
5. The frontend is unchanged (ADR 006): static files on S3 + CloudFront, no container.

## Service verdicts (this ADR overrides the table in ADR 004 where they differ)

| Service | Verdict | Note |
|---|---|---|
| VPC, subnets, security groups | REQUIRED | Public subnets for ALB and task, private for RDS, no NAT |
| ECS Fargate (1 service / 1 task / 2 containers) | REQUIRED | Task size chosen from measurements at P7 |
| ECR | REQUIRED | One image, few retained |
| ALB | REQUIRED | Stable origin for CloudFront |
| RDS PostgreSQL + pgvector, single-AZ, smallest class | REQUIRED | ADR 001 |
| S3 (frontend) + CloudFront | REQUIRED | ADR 006 |
| S3 (private documents bucket) | REQUIRED | Originals of uploads; PDFs do not belong in the database |
| Bedrock | REQUIRED | LLM and embeddings, IAM-authenticated |
| IAM roles, GitHub OIDC provider | REQUIRED | Least privilege, keyless CI/CD |
| CloudWatch Logs | REQUIRED | 7-day retention |
| SSM Parameter Store, RDS-managed secret | REQUIRED | Secrets |
| CloudWatch alarm | USEFUL | At most one (task not running) |
| EventBridge Scheduler | **REMOVED** | Replaced by the worker's timer |
| Separate worker service | **REMOVED** | Merged into the same task |
| NAT, WAF, API Gateway, Lambda, SQS, Redis, DynamoDB, OpenSearch, EKS, custom domain | UNNECESSARY | Custom domain only if Google OAuth proves it necessary |

## Destroyability rules

ADR 004's rules still hold and extend to the new bucket: `force_destroy = true` on both S3 buckets,
`force_delete = true` on ECR, no RDS final snapshot, and every secret without a recovery window. The
Terraform state bucket remains outside the destroyable configuration. Nothing is created by hand.

## Cost

Compared with ADR 004: one fewer Fargate task, no EventBridge (free at this volume anyway), plus one
near-free S3 bucket. **The itemised cost table (running cost, cost while stopped, cost after
`terraform destroy`, verdict per service) is still owed before any provisioning, at the start of the
Terraform phase (P7).** No AWS resource is created by this decision.

## Alternatives considered

| Option | Why not |
|---|---|
| Two ECS services (api, worker) | Independent scaling is irrelevant here; costs another task and more Terraform |
| EventBridge Scheduler + one-off task | Extra service, IAM role, and moving part for a timer the worker can own |
| PDFs stored in Postgres | Bloats the database and backups; S3 is the normal home for files |
| Everything in one container | Loses the API/worker process separation we want to be able to explain |

## Tradeoffs

- One task means the API and worker share CPU and memory and are deployed together.
- An `essential` worker that dies restarts the API too (a brief outage). The worker must therefore
  catch per-job exceptions so only a process-level failure takes the task down.
- The in-process timer only runs while the worker runs; acceptable for a demo deployment.
- Splitting into two services later is a configuration change, not a redesign.

## Amendments

### 2026-09-21: what P6 delivered of the two containers ([ADR 014](014-containers-and-local-compose.md))

The decision above is unchanged. This records what exists locally and what does not yet.

- **One image, several commands.** P6 delivers the `api` command (the image default) and `migrate`
  (`alembic upgrade head`) from the one backend image. Migration remains a one-off task run before the
  service starts, with the admin credentials only in that task (point 4 above).
- **The `worker` command, and its Compose service, are not part of P6.** They arrive with the first job
  type in P9. The image has no `ENTRYPOINT`, so the worker is one more `command:`.
- **The Compose topology is not the ECS topology.** Locally `db`, `migrate`, `api` and `web` are four
  Compose services; in AWS `api` and `worker` share one task, migration is a one-off ECS task, the
  database is RDS, and `web` does not exist (CloudFront and S3 replace it).

### 2026-09-22: the deployment is split by lifetime ([ADR 015](015-persistent-edge.md))

This ADR said the whole AWS stack is destroyed and recreated at will. **That is now true of everything
that costs money, but not of everything.**

A CloudFront distribution is given a new `*.cloudfront.net` domain every time it is created, and that
domain is the OAuth redirect URI registered with Google. Destroying it nightly would mean a Google
console change before every demo, waiting out a propagation delay of up to several hours.

So the deployment is split by lifetime rather than by layer. `infra/edge` -- the distribution, the
private S3 site bucket and the shared origin secret -- is applied once and kept. It has **no hourly or
monthly rate at all**, so the saving it would have produced does not exist. `infra/stack` is unchanged:
VPC, ALB, ECS, RDS, ECR and IAM still die after every session, and they are the $1.41 a day.

The two roots exchange values in one direction each: persistent to ephemeral through SSM parameters
that always exist, ephemeral to persistent through a command-line variable, because a data source must
never point at something destroyed nightly. See ADR 015 for the wiring, the verification and the
limitations.
