# Runbook — switching the demo on and off

The AWS deployment is split by lifetime ([ADR 015](decisions/015-persistent-edge.md),
[ADR 016](decisions/016-cicd-registry-and-github-identity.md)):

| | Lifetime | Cost |
|---|---|---|
| `infra/bootstrap` — Terraform state bucket | forever | under a cent a month |
| `infra/edge` — CloudFront, the static site, the origin secret | **forever** | **nothing per hour** |
| `infra/cicd` — the image registry (ECR), GitHub's OIDC identity | forever | a few cents a month |
| `infra/stack` — VPC, ALB, ECS, RDS, IAM | **destroyed after every session** | **$1.41/day idle, $1.84/day running** |

So "switching the demo on" means applying `infra/stack` and pointing the existing distribution at the
new load balancer. **The public URL never changes**, and the Google OAuth client is configured once,
already done:

> **https://<distribution>.cloudfront.net**

---

## Before you start

- **Allow 30 minutes.** A cold start is about 15 minutes of waiting; leave room for something to go
  wrong. Do not begin five minutes before an interview.
- Docker Desktop running (the image is rebuilt and pushed).
- `aws login --profile stock-analyst-admin` if the session has expired — it is interactive and needs
  MFA.
- **In PowerShell, quote any Terraform flag containing `=`.** PowerShell splits the argument at the
  `=`, and Terraform then reports "No positional arguments are expected". Write
  `terraform init "-backend-config=backend.hcl"`. A bare positional such as `terraform apply tfplan`
  is fine.

Set these once per terminal:

```powershell
$env:AWS_PROFILE = "stock-analyst-admin"
$env:TF_VAR_allowed_account_id = "<the 12-digit account id>"
Get-Content "C:\Contextual Agentic AI Indian Stock Analyst\.env" | ForEach-Object {
  if ($_ -match '^GOOGLE_CLIENT_ID=(.+)$')     { $env:TF_VAR_google_client_id = $Matches[1].Trim() }
  if ($_ -match '^GOOGLE_CLIENT_SECRET=(.+)$') { $env:TF_VAR_google_client_secret = $Matches[1].Trim() }
}
```

---

## Once: the build machinery (`infra/cicd`)

Applied once, like the edge, and never destroyed. It must exist before `infra/stack` can be planned,
because the stack reads the registry URL from the parameter it publishes.

First check the account has no GitHub identity provider already. AWS allows one per issuer, and an
existing one would have to be imported rather than created:

```powershell
aws iam list-open-id-connect-providers
```

If the list contains `token.actions.githubusercontent.com`, stop and import it before applying.
Otherwise:

```powershell
cd "C:\Contextual Agentic AI Indian Stock Analyst\infra\bootstrap"
terraform output -raw backend_config_hcl > ..\cicd\backend.hcl
```

Edit `infra\cicd\backend.hcl` so the `key` line reads `cicd/terraform.tfstate`. **Never share a key
with another root.** Then:

```powershell
cd ..\cicd
terraform init "-backend-config=backend.hcl"
terraform plan "-out=tfplan"
terraform apply tfplan
```

6 resources: the repository, its lifecycle policy, the SSM parameter, the OIDC provider, the CI role
and its policy. Note `ci_role_arn`; the P8b workflow needs it.

---

## Switching it on

Measured timings are from the 2026-09-22 drill.

### 1. Apply the application stack — about 7 minutes

```powershell
cd "C:\Contextual Agentic AI Indian Stock Analyst\infra\stack"
terraform plan "-out=tfplan"
terraform apply tfplan
```

49 resources (51 before P8a moved the registry to `infra/cicd`). RDS takes about 6 minutes of it; the load balancer about 2, in parallel.

**Delete `tfplan` afterwards — a plan file contains the Google client secret in plaintext.**

Note the `alb_dns_name` output. You need it in step 5.

### 2. Push the backend image — about 1 minute, only if it changed

The repository lives in `infra/cicd` and survives teardown, so the last image pushed is still there.
Push only after changing the backend (until P8b, when CI pushes on every commit to `main`). The
registry address is `terraform output ecr_repository_url` in `infra/cicd`, without the repository
name.

```powershell
cd "C:\Contextual Agentic AI Indian Stock Analyst"
$reg = "<account>.dkr.ecr.ap-south-1.amazonaws.com"
docker build -t stock-analyst-backend:latest backend
aws ecr get-login-password --region ap-south-1 | docker login --username AWS --password-stdin $reg
docker tag stock-analyst-backend:latest "$reg/stock-analyst-demo-backend:latest"
docker push "$reg/stock-analyst-demo-backend:latest"
```

### 3. Create the schema — about 1 minute

```powershell
$net = '{"awsvpcConfiguration":{"subnets":["<public-subnet-1>","<public-subnet-2>"],"securityGroups":["<task-sg>"],"assignPublicIp":"ENABLED"}}'
aws ecs run-task --cluster stock-analyst-demo --task-definition stock-analyst-demo-migrate `
  --launch-type FARGATE --network-configuration $net
```

Subnet and security-group ids come from the stack's outputs. Wait for it to stop, then check the exit
code is 0 — `aws ecs describe-tasks --cluster stock-analyst-demo --tasks <arn> --query 'tasks[0].containers[0].exitCode'`.

### 4. Create the runtime role — about 1 minute

```powershell
aws ecs run-task --cluster stock-analyst-demo --task-definition stock-analyst-demo-provision `
  --launch-type FARGATE --network-configuration $net
```

RDS has no `docker-entrypoint-initdb.d`, so nothing else creates the no-DDL `stock_app` role that
ADR 011 requires. Without this the site loads and `/api/healthz` passes, but every real query fails.
It is idempotent and order-independent, so it may run before or after step 3, and twice if you like.

### 5. Point CloudFront at the new load balancer — 33 seconds

```powershell
cd "C:\Contextual Agentic AI Indian Stock Analyst\infra\edge"
terraform plan "-out=tfplan" "-var=alb_origin_domain=<the alb_dns_name from step 1>"
terraform apply tfplan
```

The load balancer's name changes on every rebuild; the public URL does not. This is invisible to
Google.

### 6. Start the application — about 45 seconds to healthy

```powershell
aws ecs update-service --cluster stock-analyst-demo --service stock-analyst-demo-api --desired-count 1
```

Wait for the target group to report `healthy`, then check the whole path from outside:

```powershell
curl.exe -s -o NUL -w "%{http_code}`n" https://<distribution>.cloudfront.net/
curl.exe -s https://<distribution>.cloudfront.net/api/readyz
```

`/api/readyz` returning `{"status":"ready"}` is the real green light: it proves the api can reach RDS
as the runtime role. If it fails, step 4 did not run.

### 7. Upload the frontend — only if it changed

The static site survives teardown, so this is usually unnecessary.

```powershell
cd "C:\Contextual Agentic AI Indian Stock Analyst\frontend"
npm run build
aws s3 sync out "s3://stock-analyst-demo-site-<account>/" --delete
aws cloudfront create-invalidation --distribution-id <distribution-id> --paths "/*"
```

---

## Switching it off — do this every time

```powershell
cd "C:\Contextual Agentic AI Indian Stock Analyst\infra\stack"
terraform destroy
```

**Only in `infra\stack`.** Destroying `infra\edge` throws away the permanent domain and the Google
registration with it. The plan should say **49 to destroy**; if it says 11 (the edge) or 6 (the build
machinery), you are in the wrong directory — stop.

About 5 minutes. Then confirm nothing expensive survived:

```powershell
aws ecs list-clusters --query clusterArns
aws rds describe-db-instances --query "DBInstances[].DBInstanceIdentifier"
aws elbv2 describe-load-balancers --query "LoadBalancers[].LoadBalancerName"
aws ec2 describe-vpcs --filters Name=isDefault,Values=false --query "Vpcs[].VpcId"
aws ec2 describe-addresses --query "Addresses[].PublicIp"
aws rds describe-db-snapshots --query "DBSnapshots[].DBSnapshotIdentifier"
```

All six must be empty. Then confirm the edge is untouched: `https://<distribution>.cloudfront.net/`
still returns 200, and `/api/healthz` returns 502. That pair is the correct resting state.

Two things you will see afterwards that are **not** costs: deregistered ECS task-definition revisions,
which are listed forever by design and cannot be removed; and stale entries in the Resource Groups
Tagging API, which lags. Trust the six commands above instead.

---

## When something goes wrong

| Symptom | Cause |
|---|---|
| `/api/*` returns **502** | No application running, or CloudFront still points at the previous load balancer. Do step 5. |
| `/api/*` returns **403** | The `X-Origin-Verify` header does not match the listener rule. Re-apply the edge so both ends read the same parameter. |
| `/api/readyz` returns **503** | The `stock_app` role is missing. Run step 4. |
| The api task keeps restarting | A setting the backend demands is missing. `aws logs get-log-events` on `/stock-analyst/demo/api` prints the Pydantic validation error naming it. |
| Sign-in fails with `redirect_uri_mismatch` | Only possible if the distribution was recreated. Compare the Google client's URI with `terraform output public_base_url` in `infra/edge`. |
| `terraform plan` in `infra/stack` fails on a data source | `infra/edge` or `infra/cicd` has not been applied. Both are permanent and must exist. |
| The api task fails with `CannotPullContainerError` | The registry is empty: push the image (step 2). |

## What it costs

| State | Per day |
|---|---|
| Off — edge and registry only | **~$0.00** (a few cents a month for stored images) |
| Stack applied, `desired_count = 0` | $1.41 |
| Running | $1.84 |

A typical drill of an hour or so is about **$0.10**. Nothing in the persistent tiers has an hourly rate.
