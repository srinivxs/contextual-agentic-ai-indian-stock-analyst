# Runbook — switching the demo on and off

The AWS deployment is split by lifetime ([ADR 015](decisions/015-persistent-edge.md),
[ADR 016](decisions/016-cicd-registry-and-github-identity.md)):

| | Lifetime | Cost |
|---|---|---|
| `infra/bootstrap` — Terraform state bucket | forever | under a cent a month |
| `infra/edge` — CloudFront, the static site, the origin secret | **forever** | **nothing per hour** |
| `infra/cicd` — the image registry (ECR), GitHub's OIDC identity | forever | a few cents a month |
| `infra/stack` — VPC, ALB, ECS (api + worker), RDS, the documents bucket, IAM | **destroyed after every session** | **$1.41/day idle, about $2.30/day running** |

So "switching the demo on" means applying `infra/stack` and pointing the existing distribution at the
new load balancer. **The public URL never changes**, and the Google OAuth client is configured once,
already done:

> **`https://<distribution>.cloudfront.net`**, printed by `terraform output public_base_url` in `infra/edge`

---

## Before you start

- **Allow 30 minutes.** A cold start is about 15 minutes of waiting; leave room for something to go
  wrong. Do not begin five minutes before an interview.
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
  if ($_ -match '^ALLOWED_EMAILS=(.+)$')       { $env:TF_VAR_allowed_emails = $Matches[1].Trim() }
}
```

---

## Once: the build machinery (`infra/cicd`)

Applied once, like the edge, and never destroyed. It must exist before `infra/stack` can be planned,
because the stack reads the registry URL from the parameter it publishes. Since P8c it also reads the
site bucket and distribution id that `infra/edge` publishes, so **apply the edge before this root**.

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

8 resources: the repository, its lifecycle policy, the SSM parameter, the OIDC provider, the push
role and its policy, the deploy role and its policy. The workflow reads `ci_role_arn` and
`deploy_role_arn` from the repository secrets `CI_ROLE_ARN` and `DEPLOY_ROLE_ARN` (secrets, not
variables, so the account number never appears in the public logs).

---

## Switching it on

**The quick way (since P8):** one script runs steps 1 to 6 below, stopping at each Terraform plan
for your `yes`, and finishes when `/api/readyz` is ready. It writes no plan file.

```powershell
& "C:\Contextual Agentic AI Indian Stock Analyst\scripts\demo-up.ps1"
```

Run `aws login --profile stock-analyst-admin` first if the session has expired. **Do not push to
`main` while it runs:** a deploy would start a second migration alongside the script's. If RDS
reports no capacity for `db.t4g.micro`, run the script again: Terraform keeps what it built, and since
P8 the database may use any of the three zones. The steps below are
what it does, for when one of them needs doing by hand. Measured timings are from the 2026-09-22
drill.

### 1. Apply the application stack — about 7 minutes

```powershell
cd "C:\Contextual Agentic AI Indian Stock Analyst\infra\stack"
terraform plan "-out=tfplan"
terraform apply tfplan
```

49 resources (51 before P8a moved the registry to `infra/cicd`). RDS takes about 6 minutes of it; the load balancer about 2, in parallel.

**Delete `tfplan` afterwards — a plan file contains the Google client secret in plaintext.**

Note the `alb_dns_name` output. You need it in step 5.

### 2. The backend image — nothing to do

Since P8b, GitHub Actions builds and pushes an image on every push to `main` whose checks pass, tagged
with the commit SHA, into the permanent repository in `infra/cicd`. Step 1's plan picks the newest one
by digest. To run an older commit instead, add `"-var=image_tag=<full commit sha>"` to the plan (the
last five images are kept). If the plan fails with "no matching image", no pipeline run has pushed yet:
check the Actions tab.

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

### 5. Point CloudFront at the new load balancer — 1 to 7 minutes

```powershell
cd "C:\Contextual Agentic AI Indian Stock Analyst\infra\edge"
terraform plan "-out=tfplan" "-var=alb_origin_domain=<the alb_dns_name from step 1>"
terraform apply tfplan
```

The load balancer's name changes on every rebuild; the public URL does not. This is invisible to
Google. CloudFront copies the change to every edge location before Terraform returns: 33 seconds on
2026-09-22, 7 minutes on 2026-09-23. Do not interrupt it.

### 6. Start the application — about 45 seconds to healthy

```powershell
aws ecs update-service --cluster stock-analyst-demo --service stock-analyst-demo-api --desired-count 1
```

Wait for the target group to report `healthy`, then check the whole path from outside:

```powershell
$site = terraform "-chdir=infra/edge" output -raw public_base_url
curl.exe -s -o NUL -w "%{http_code}`n" "$site/"
curl.exe -s "$site/api/readyz"
```

`/api/readyz` returning `{"status":"ready"}` is the real green light: it proves the api can reach RDS
as the runtime role. If it fails, step 4 did not run.

### 6b. The data: restored, then topped up (since 2026-10-08)

The database is **restored from the newest save** `demo-down.ps1` made (ADR 008 amendment), so the
app has its filings, facts, search, conversations and price history from the first minute; the
worker only fetches what is new since then, usually a few minutes. With no save yet (the very first
session, or after the saves were deleted) it starts empty and the worker refills it in about 30 to
45 minutes. The same task runs the **worker** beside the api. With the defaults
(`data_sources_enabled` and `ai_enabled` both true) the worker, by itself:

1. reads each stock's screener.in page and fetches about 85 filings from BSE into the documents
   bucket (2 s apart), with the fundamentals table and top ratios;
2. fetches the last month of BSE daily price files (8 per run, 30 s apart; declared BSE holidays are skipped) and the RBI feed;
3. turns each filing into page text and passages, then fingerprints them with Titan (about $0.07)
   and reads them for facts and events with Nova 2 Lite (about $0.49, capped by
   `extraction_budget_usd`, default $2).

Watch it with:

```powershell
aws logs tail /stock-analyst/demo/worker --follow --since 10m
```

The app works from the first minute; search, facts, news and the chat get richer as the worker
goes. To run without spending on Bedrock, apply with `-var ai_enabled=false`; to run fully offline
from the outside world, `-var data_sources_enabled=false`.

### 7. The frontend — nothing to do

Since P8c the pipeline publishes the static export on every push to `main`, whether or not the stack
is up (the edge is permanent), and a push while the stack is up also migrates and rolls out the
backend ([ADR 017](decisions/017-deploy-pipeline.md)). Watch it in the Actions tab.

---

## Switching it off — do this every time

**The quick way:** saves the database (a snapshot named by demo-up), destroys only `infra/stack`,
keeps the newest two saves, then runs the six checks below and says whether anything is still
billing.

```powershell
& "C:\Contextual Agentic AI Indian Stock Analyst\scripts\demo-down.ps1"
```

By hand:

```powershell
cd "C:\Contextual Agentic AI Indian Stock Analyst\infra\stack"
terraform destroy
```

**Only in `infra\stack`.** Destroying `infra\edge` throws away the permanent domain and the Google
registration with it. The plan should say **49 to destroy**; if it says 11 (the edge) or 6 (the build
machinery), you are in the wrong directory — stop.

About 8 to 10 minutes: saving the database adds a few. By hand, without demo-up's name, nothing
is saved and the next session starts empty. Then confirm nothing expensive survived:

```powershell
aws ecs list-clusters --query clusterArns
aws rds describe-db-instances --query "DBInstances[].DBInstanceIdentifier"
aws elbv2 describe-load-balancers --query "LoadBalancers[].LoadBalancerName"
aws ec2 describe-vpcs --filters Name=isDefault,Values=false --query "Vpcs[].VpcId"
aws ec2 describe-addresses --query "Addresses[].PublicIp"
aws rds describe-db-snapshots --snapshot-type manual --query "DBSnapshots[].DBSnapshotIdentifier"
```

The first five must be empty. The last lists the kept saves (`stock-analyst-demo-db-...`, at most
two, a few cents a month); anything else there is a leftover. Then confirm the edge is untouched: the site address
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
| The plan fails with "no matching image" | The registry is empty: no pipeline run has pushed yet (Actions tab). |

## What it costs

| State | Per day |
|---|---|
| Off — edge and registry only | **~$0.00** (a few cents a month for stored images) |
| Stack applied, `desired_count = 0` | $1.41 |
| Running (api + worker, 0.5 vCPU / 2 GB) | about $2.30 |

Each session also pays once for Bedrock as the worker reads the filings into the fresh database:
about **$0.56** (fingerprints $0.07, reading $0.49), plus a fraction of a cent per chat question
(capped by `chat_budget_usd`, default $1). A 12-hour session is therefore about **$1.70**. Nothing in
the persistent tiers has an hourly rate.
