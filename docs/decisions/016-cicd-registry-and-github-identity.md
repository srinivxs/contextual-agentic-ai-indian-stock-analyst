# 016 — A permanent registry and a push-only GitHub identity

- **Status:** Proposed (P8a; becomes Accepted after the first real apply. Amends
  [ADR 015](015-persistent-edge.md), which put ECR in the ephemeral `infra/stack`. Builds on
  [ADR 008](008-minimal-aws-architecture.md) and [ADR 014](014-containers-and-local-compose.md))
- **Date:** 2026-09-23

## Context

P8 adds CI/CD: GitHub Actions builds the backend image on every push to `main`, pushes it to ECR and,
later, deploys it. Two things it depends on did not fit the P7 layout.

1. **The registry was in the ephemeral stack.** `infra/stack` is destroyed after every session, which
   took the ECR repository with it. For most of any day there would be nowhere to push, and every cold
   start began by pushing an image by hand (runbook step "the ECR repository is recreated empty").
2. **GitHub had no way to authenticate to AWS.** The project rule is no stored AWS keys: CI must use
   OIDC and an IAM role.

## Decision

### 1. A fourth permanent root, `infra/cicd`

| Root | Lifetime | Contents |
|---|---|---|
| `infra/bootstrap` | forever | Terraform state bucket |
| `infra/edge` | forever | CloudFront, static site, origin secret (ADR 015) |
| **`infra/cicd`** | **forever** | **ECR repository + lifecycle policy, GitHub OIDC provider, push-only CI role** |
| `infra/stack` | destroyed after every session | VPC, ALB, ECS, RDS, IAM for the tasks, logs, app secrets |

The same rule as ADR 015: things with no hourly rate that something external depends on are kept.
ECR storage is $0.10 per GB-month and the lifecycle policy keeps five images of about 71 MB, a few
cents a month. IAM objects are free.

It is a separate root from `infra/edge` because the two do unrelated jobs. The edge is the front door
a browser uses; this is build machinery no visitor touches.

### 2. The stack reads the registry URL through SSM

`infra/cicd` publishes `/stock-analyst/demo/ecr_repository_url` (a `String`, not a secret), and
`infra/stack/registry.tf` reads it with a data source. This is the persistent-to-ephemeral direction
ADR 015 already uses for the edge's values. A plan of `infra/stack` now fails if `infra/cicd` was
never applied; that is intended. The repository keeps its name, `stock-analyst-demo-backend`, so the
task definitions and push commands do not change.

### 3. GitHub authenticates with OIDC, and the trust policy checks the subject

- One `aws_iam_openid_connect_provider` for `https://token.actions.githubusercontent.com`, audience
  `sts.amazonaws.com`, no pinned thumbprint.
- The role `stock-analyst-demo-ci` trusts that provider only when **both**
  `token.actions.githubusercontent.com:aud == sts.amazonaws.com` **and**
  `token.actions.githubusercontent.com:sub == repo:<owner>/<name>:ref:refs/heads/main`, using
  `StringEquals`. Checking only the issuer would let any repository on GitHub, forks included, assume
  the role.

### 4. Push and deploy are separate roles

The CI role can push to this one repository and call `ecr:GetAuthorizationToken`, which AWS only
allows with `Resource: "*"`. It has no ECS, S3, CloudFront, RDS, SSM or IAM permissions. Deploying
changes a running system, so it gets its own role in P8c, written against what the deploy job
actually does.

## Consequences

- A cold start no longer begins with a hand push: the image CI built is already there.
- Four Terraform roots, three of them permanent. `infra/cicd` must be applied before `infra/stack`
  can be planned.
- The role ARN can go in the workflow file in plain text: without a token GitHub mints only for this
  repository's `main`, it is useless.
- Tags stay `MUTABLE` while images are still pushed by hand as `latest`. P8b decides whether SHA tags
  plus `IMMUTABLE` replace that.

## Verification

Offline, with no AWS: `infra/cicd/tests/cicd.tftest.hcl` (mock provider) and
`infra/tests/test_infra_hygiene.py` (the registry left the stack; the trust policy names the
repository and ref; the push policy grants no other service). What they cannot prove is that GitHub
can actually assume the role, which only a real workflow run shows. That is the first job of P8b.

## Known limitations

- **One OIDC provider per issuer per account.** If one already exists, the apply fails with
  `EntityAlreadyExists` and it must be imported.
- **A job that declares a GitHub `environment:` gets a different `sub`**
  (`repo:<owner>/<name>:environment:<name>`) and is refused. It fails closed; the condition is changed
  deliberately if environments are ever used.
- Only pushes to `main` can assume the role, so pull-request builds (if the project ever uses PRs) can
  test but not push. That matches the open push-vs-PR decision for P8.
