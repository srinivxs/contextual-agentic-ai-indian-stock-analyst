# 004 — ECS Fargate and the minimum AWS footprint

- **Status:** Accepted, **partially superseded by [ADR 008](008-minimal-aws-architecture.md)**
  (2026-09-20): one service/task with two containers, no EventBridge Scheduler, and a second private
  S3 bucket for uploaded documents. Where the two differ, ADR 008 wins; the destroyability rules and
  the cost approach below still apply.
- **Date:** 2026-09-19

## Context

This is a **portfolio/interview project**, not a SaaS. It must be genuinely
deployed on AWS for live demonstration, but the owner wants minimum reasonable
cost, **no always-on spend after demos**, clean `terraform destroy`, and the
ability to recreate everything later with `terraform apply`. Cost is therefore
mostly *hourly while the stack exists*; teardown makes idle cost near zero.

## Decision

- **Compute:** Amazon **ECS on Fargate**. One image, run as an `api` service
  (1 task), a `worker` service (1 task, `desired_count` adjustable, including 0),
  and a scheduled one-off task (`enqueue-due`). No autoscaling.
- **Ingress:** CloudFront → ALB (API) and CloudFront → S3 (static SPA).
- **Networking:** public subnets for ALB and tasks (tasks get public IPs; their
  security group accepts traffic only from the ALB); private subnets for RDS.
  **No NAT Gateway.**
- **Secrets:** SSM Parameter Store SecureString for app secrets; RDS-managed
  master password in Secrets Manager.
- Everything is created by Terraform and must be **destroyable** (see rules below).

## Alternatives considered

| Option | Why not |
|---|---|
| Lambda + API Gateway | Cold starts with LangGraph imports; needs connection management (RDS Proxy); a long-running worker fits poorly. |
| EC2 / Lightsail | Weaker "production-grade AWS" demonstration; manual patching; less like real deployments. |
| EKS | Explicitly excluded: large fixed cost and complexity. |
| Render/Railway | Accepted by the challenge but ranked lower; the goal here is real AWS + Terraform. |

## AWS service classification and cost

Prices are **approximate on-demand list prices (us-east-1), from memory, and must
be verified against current AWS pricing before `apply`.** Cost is per hour while up.

| Service | Verdict | Role and approximate cost |
|---|---|---|
| VPC, subnets, IGW, security groups | REQUIRED | Network isolation. Free. |
| ECS cluster + Fargate | REQUIRED | Runs api/worker. ~$0.012/hr per 0.25 vCPU/0.5 GB task (~$9/mo if always on). |
| ECR | REQUIRED | Image storage. ~$0.10/GB-month; keep last ~5 images. Cents. |
| ALB | REQUIRED | Stable origin for CloudFront, health checks. ~$0.0225/hr + usage (~$0.55/day). Alternatives lack a stable DNS name or force different app packaging. |
| RDS PostgreSQL (db.t4g.micro, single-AZ) | REQUIRED | The data store (ADR 001). ~$0.016/hr + ~20 GB gp3 storage. Destroyed after demos. |
| Public IPv4 addresses | REQUIRED (side effect) | Billed ~$0.005/hr each (ALB + task IPs). ~$0.5/day total. Avoids NAT. |
| S3 (frontend bucket) | REQUIRED | Static SPA hosting. Cents. |
| CloudFront | REQUIRED | HTTPS entry, SPA + API on one origin (no CORS). Within free tier at this volume. |
| Bedrock | REQUIRED | LLM + embeddings, IAM-authenticated. Pay per token; usage-driven. Models verified before choosing. |
| IAM roles + GitHub OIDC provider | REQUIRED | Least-privilege task roles and keyless CI/CD. Free. |
| CloudWatch Logs | REQUIRED | Debugging. 7-day retention; ingestion ~$0.50/GB (tiny). |
| S3 Terraform state bucket | REQUIRED | Created once, **outside** the destroyable config, so recreation works. Cents. |
| SSM Parameter Store (SecureString, standard) | USEFUL | App secrets. Free tier; deletes instantly on destroy. |
| Secrets Manager (RDS-managed password only) | USEFUL | Avoids a password in Terraform state. ~$0.40/secret-month; `recovery_window = 0` on destroy. |
| EventBridge Scheduler | USEFUL | Demonstrates scheduled refresh (cron → RunTask). Free at this volume. |
| CloudWatch alarms (≤3) | USEFUL | ALB 5xx, task count, RDS storage. ~$0.10 each; no SNS/dashboards. |
| Route 53 / ACM / custom domain | UNNECESSARY (until proven) | Only added if Google OAuth genuinely rejects the CloudFront URL (tested in P7, per D5). |
| NAT Gateway, interface VPC endpoints | UNNECESSARY | ~$32+/mo each while up; public tasks avoid them. |
| WAF | UNNECESSARY | Per-user quotas in the DB cover the real cost risk. |
| API Gateway, Lambda, SQS, DynamoDB, OpenSearch, ElastiCache/Redis, EKS, Kafka | UNNECESSARY | Excluded; no requirement needs them. |
| Container Insights, X-Ray, AgentCore | UNNECESSARY | Extra cost/complexity without a requirement. |

**Rough estimate:** ~$2–3 per day while up (a demo week is on the order of tens
of dollars); pennies while destroyed (state bucket, a few ECR images). To be
re-verified in P6.

## Destroyability rules (must hold for all Terraform)

1. **Everything** app-related is Terraform-managed; nothing created by hand.
2. S3 buckets: `force_destroy = true`. ECR: `force_delete = true`.
3. RDS: `deletion_protection = false`, `skip_final_snapshot = true` (data is
   re-ingestable and reproducible from public sources).
4. Secrets Manager secrets: `recovery_window_in_days = 0`.
5. The Terraform state bucket is bootstrapped separately and survives destroys.
6. No always-on extras; `worker` may run at `desired_count = 0`.

## Tradeoffs

- Tasks with public IPs are less locked-down than private tasks behind NAT;
  compensated by SG rules. Documented as a deliberate cost decision.
- CloudFront → ALB is HTTP unless a domain/cert is added; accepted for a demo,
  mitigated with the CloudFront prefix-list SG rule and a secret origin header.
- Fargate bills while idle; teardown is the mitigation.
