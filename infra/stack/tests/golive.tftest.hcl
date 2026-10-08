# Offline tests for go-live (GL): everything the application needs in AWS beyond the P7/P8 shell.
#
#   1. a private bucket for the filings the worker fetches (documents.tf)
#   2. the worker container, beside the api in the same task (compute.tf, ADR 008)
#   3. a task big enough for both
#   4. the task role: the documents bucket, and Bedrock's two models (identity.tf, ADR 010)
#   5. the feature switches and spending caps, as variables (variables.tf)
#
# HOW TO RUN IT
#   terraform test        (no AWS: every provider call goes to the mock below)
#
# WHAT THESE TESTS CANNOT PROVE
#   That Bedrock accepts the policy for the global inference profile, that the worker reaches BSE
#   from a public subnet, or that 2 GB is enough for the largest annual report. Those need the
#   first real session; the runbook's go-live checklist says what to look at.

mock_provider "aws" {
  mock_data "aws_ssm_parameter" {
    defaults = {
      value = "https://mock-distribution.cloudfront.net"
    }
  }

  mock_data "aws_availability_zones" {
    defaults = {
      names = ["ap-south-1a", "ap-south-1b", "ap-south-1c"]
    }
  }

  mock_data "aws_ec2_managed_prefix_list" {
    defaults = {
      id = "pl-0mockcloudfront"
    }
  }

  mock_data "aws_kms_alias" {
    defaults = {
      target_key_arn = "arn:aws:kms:ap-south-1:123456789012:key/mock-ssm-key"
    }
  }

  mock_resource "aws_db_instance" {
    defaults = {
      address  = "mock-db.ap-south-1.rds.amazonaws.com"
      endpoint = "mock-db.ap-south-1.rds.amazonaws.com:5432"
    }
  }
}

override_data {
  target = data.aws_ssm_parameter.ecr_repository_url
  values = {
    value = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/stock-analyst-demo-backend"
  }
}

override_data {
  target = data.aws_ecr_image.backend
  values = {
    image_digest = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
  }
}

# Distinct, valid ARNs per resource (see compute.tftest.hcl for why override, not mock).
override_resource {
  target = aws_iam_role.api_execution
  values = {
    arn = "arn:aws:iam::123456789012:role/mock-api-execution"
  }
}

override_resource {
  target = aws_iam_role.migrate_execution
  values = {
    arn = "arn:aws:iam::123456789012:role/mock-migrate-execution"
  }
}

override_resource {
  target = aws_iam_role.task
  values = {
    arn = "arn:aws:iam::123456789012:role/mock-task"
  }
}

override_resource {
  target = aws_lb.main
  values = {
    arn      = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:loadbalancer/app/mock-alb/0123456789abcdef"
    dns_name = "mock-alb-1234567890.ap-south-1.elb.amazonaws.com"
  }
}

override_resource {
  target = aws_lb_target_group.api
  values = {
    arn = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:targetgroup/mock-api/0123456789abcdef"
  }
}

override_resource {
  target = aws_lb_listener.http
  values = {
    arn = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:listener/app/mock-alb/0123456789abcdef/0123456789abcdef"
  }
}

override_resource {
  target = aws_ecs_cluster.main
  values = {
    arn = "arn:aws:ecs:ap-south-1:123456789012:cluster/stock-analyst-demo"
  }
}

override_resource {
  target = aws_s3_bucket.documents
  values = {
    id  = "stock-analyst-demo-documents-123456789012"
    arn = "arn:aws:s3:::stock-analyst-demo-documents-123456789012"
  }
}

variables {
  allowed_account_id   = "123456789012"
  google_client_id     = "mock-client-id.apps.googleusercontent.com"
  google_client_secret = "mock-google-client-secret"
  allowed_emails       = "owner@example.com,friend@example.org"
}

# --- 1. the documents bucket ------------------------------------------------------------------------

run "the_documents_bucket_is_named_for_this_project_and_account" {
  assert {
    condition     = aws_s3_bucket.documents.bucket == "stock-analyst-demo-documents-123456789012"
    error_message = "One bucket per account: stock-analyst-demo-documents-<account id>."
  }

  # The filings are public documents re-fetched from BSE each session, and the stack is destroyed
  # after every session. Without this, destroy stops at a bucket that is not empty.
  assert {
    condition     = aws_s3_bucket.documents.force_destroy == true
    error_message = "The bucket must empty itself on destroy, or demo-down fails."
  }
}

run "the_documents_bucket_can_never_be_made_public" {
  assert {
    condition = (
      aws_s3_bucket_public_access_block.documents.block_public_acls &&
      aws_s3_bucket_public_access_block.documents.block_public_policy &&
      aws_s3_bucket_public_access_block.documents.ignore_public_acls &&
      aws_s3_bucket_public_access_block.documents.restrict_public_buckets
    )
    error_message = "All four public-access blocks must be on: stored files are never served back."
  }

  assert {
    condition     = aws_s3_bucket_ownership_controls.documents.rule[0].object_ownership == "BucketOwnerEnforced"
    error_message = "ACLs off: access is decided by IAM alone."
  }
}

run "the_documents_bucket_encrypts_and_refuses_plain_http" {
  assert {
    condition = anytrue([
      for rule in aws_s3_bucket_server_side_encryption_configuration.documents.rule :
      anytrue([for d in rule.apply_server_side_encryption_by_default : d.sse_algorithm == "AES256"])
    ])
    error_message = "Every object is encrypted at rest with S3's own key (no KMS cost)."
  }

  assert {
    condition = anytrue([
      for s in jsondecode(aws_s3_bucket_policy.documents.policy).Statement :
      s.Effect == "Deny" && s.Condition.Bool["aws:SecureTransport"] == "false"
    ])
    error_message = "The bucket policy must refuse any request not made over TLS."
  }
}

# --- 2 and 3. the worker container, and a task that fits both ------------------------------------------

run "the_task_is_big_enough_for_the_api_and_the_worker" {
  # 0.25 vCPU / 0.5 GB ran the api alone. The worker reads PDFs of up to 60 MB into memory, so the
  # task grows to 0.5 vCPU / 2 GB (about $0.04 an hour with both).
  assert {
    condition     = aws_ecs_task_definition.api.cpu == "512" && aws_ecs_task_definition.api.memory == "2048"
    error_message = "The api task must be 0.5 vCPU and 2 GB once the worker shares it."
  }
}

run "the_worker_runs_beside_the_api_from_the_same_image" {
  assert {
    condition     = [for c in jsondecode(aws_ecs_task_definition.api.container_definitions) : c.name] == ["api", "worker"]
    error_message = "One task, two containers: api first (the load balancer targets it), then worker."
  }

  assert {
    condition     = jsondecode(aws_ecs_task_definition.api.container_definitions)[1].command == ["python", "-m", "app.worker"]
    error_message = "The worker container runs python -m app.worker, as in Compose."
  }

  assert {
    condition = anytrue([
      for env in jsondecode(aws_ecs_task_definition.api.container_definitions)[1].environment :
      env.name == "PYTHONPATH" && env.value == "/app/src"
    ])
    error_message = "python -m needs PYTHONPATH=/app/src: the code is not in WORKDIR."
  }

  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.api.container_definitions)[1].image ==
      jsondecode(aws_ecs_task_definition.api.container_definitions)[0].image
    )
    error_message = "The worker and the api are one image, one build."
  }
}

run "a_worker_crash_restarts_the_worker_and_leaves_the_api_running" {
  # ADR 008's stated trade-off was "a worker crash restarts the API". ECS can now restart one
  # container in place, so the worker is not essential and restarts itself instead.
  assert {
    condition     = jsondecode(aws_ecs_task_definition.api.container_definitions)[1].essential == false
    error_message = "The worker must not be essential: its crash must not stop the api."
  }

  assert {
    condition     = jsondecode(aws_ecs_task_definition.api.container_definitions)[1].restartPolicy.enabled == true
    error_message = "The worker restarts itself in place, like restart: unless-stopped in Compose."
  }

  assert {
    condition     = jsondecode(aws_ecs_task_definition.api.container_definitions)[0].essential == true
    error_message = "The api stays essential: without it the task has no reason to run."
  }

  # A hard limit on the worker, so a runaway PDF cannot starve the api of the task's memory.
  assert {
    condition     = jsondecode(aws_ecs_task_definition.api.container_definitions)[1].memory == 1536
    error_message = "The worker is capped at 1.5 GB of the task's 2 GB."
  }
}

run "the_worker_gets_the_runtime_url_and_no_login_secrets" {
  assert {
    condition = [
      for s in jsondecode(aws_ecs_task_definition.api.container_definitions)[1].secrets : s.name
    ] == ["DATABASE_URL"]
    error_message = "The worker needs the runtime database URL and nothing else (no Google or session secret)."
  }

  assert {
    condition = alltrue([
      for s in jsondecode(aws_ecs_task_definition.api.container_definitions)[1].secrets :
      s.valueFrom == "arn:aws:ssm:ap-south-1:123456789012:parameter/stock-analyst/demo/database_url"
    ])
    error_message = "Never the admin URL: the worker runs as the no-DDL role, like the api."
  }

  assert {
    condition     = !can(jsondecode(aws_ecs_task_definition.api.container_definitions)[1].portMappings[0])
    error_message = "The worker listens on nothing."
  }
}

run "the_worker_keeps_filings_in_the_documents_bucket_and_logs_to_its_own_group" {
  assert {
    condition = anytrue([
      for env in jsondecode(aws_ecs_task_definition.api.container_definitions)[1].environment :
      env.name == "BLOB_BUCKET" && env.value == aws_s3_bucket.documents.bucket
    ])
    error_message = "BLOB_BUCKET must name the documents bucket, or filings go to the container's disk."
  }

  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.api.container_definitions)[1].logConfiguration.options["awslogs-group"] ==
      aws_cloudwatch_log_group.worker.name
    )
    error_message = "The worker logs to its own group."
  }

  assert {
    condition     = aws_cloudwatch_log_group.worker.retention_in_days == var.log_retention_days
    error_message = "Worker logs expire like the others."
  }
}

# --- 4. the task role ---------------------------------------------------------------------------------

run "the_task_role_may_read_and_write_filings_and_nothing_else_in_s3" {
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.task_documents.policy).Statement :
      s.Resource == "arn:aws:s3:::stock-analyst-demo-documents-123456789012/documents/*"
    ])
    error_message = "Only objects under documents/ in this bucket."
  }

  assert {
    condition = toset(flatten([
      for s in jsondecode(aws_iam_role_policy.task_documents.policy).Statement : s.Action
    ])) == toset(["s3:GetObject", "s3:PutObject"])
    error_message = "Get and put only: no delete, no list, no bucket settings."
  }

  assert {
    condition     = aws_iam_role_policy.task_documents.role == aws_iam_role.task.id
    error_message = "This is the application's role, not an execution role."
  }
}

run "the_task_role_may_call_exactly_the_two_bedrock_models" {
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.task_bedrock.policy).Statement :
      s.Action == "bedrock:InvokeModel"
    ])
    error_message = "InvokeModel only (Converse uses it); no streaming, no model management."
  }

  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.task_bedrock.policy).Statement :
      s.Resource == "arn:aws:bedrock:ap-south-1::foundation-model/amazon.titan-embed-text-v2:0"
    ])
    error_message = "Titan Text Embeddings V2, in Mumbai."
  }

  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.task_bedrock.policy).Statement :
      s.Resource == "arn:aws:bedrock:ap-south-1:123456789012:inference-profile/global.amazon.nova-2-lite-v1:0"
    ])
    error_message = "Nova 2 Lite through the global inference profile (ADR 010)."
  }

  # ADR 010: a global profile also needs the source-region model and the regionless global model,
  # each allowed ONLY through that profile.
  assert {
    condition = alltrue([
      for arn in [
        "arn:aws:bedrock:ap-south-1::foundation-model/amazon.nova-2-lite-v1:0",
        "arn:aws:bedrock:::foundation-model/amazon.nova-2-lite-v1:0",
        ] : anytrue([
          for s in jsondecode(aws_iam_role_policy.task_bedrock.policy).Statement :
          s.Resource == arn &&
          s.Condition.StringEquals["bedrock:InferenceProfileArn"] == "arn:aws:bedrock:ap-south-1:123456789012:inference-profile/global.amazon.nova-2-lite-v1:0"
      ])
    ])
    error_message = "The Nova models are reachable only through the inference profile."
  }
}

# --- 5. the feature switches and the spending caps -------------------------------------------------------

run "by_default_everything_is_on_in_both_containers" {
  assert {
    condition = alltrue([
      for c in jsondecode(aws_ecs_task_definition.api.container_definitions) :
      alltrue([
        for pair in [
          ["FILINGS_DISCOVERY", "true"],
          ["PRICES_ENABLED", "true"],
          ["FEED_MODE", "live"],
          ["EMBEDDINGS_ENABLED", "true"],
          ["EXTRACTION_ENABLED", "true"],
          ["CHAT_ENABLED", "true"],
        ] : anytrue([for env in c.environment : env.name == pair[0] && env.value == pair[1]])
      ])
    ])
    error_message = "Both containers get the same switches, all on: this is the live demo."
  }

  assert {
    condition = alltrue([
      for c in jsondecode(aws_ecs_task_definition.api.container_definitions) :
      anytrue([for env in c.environment : env.name == "CHAT_BUDGET_USD" && env.value == "5"]) &&
      anytrue([for env in c.environment : env.name == "EXTRACTION_BUDGET_USD" && env.value == "10"])
    ])
    error_message = "The caps the owner approved (2026-10-09): $5 for chat, $10 for extraction."
  }
}

run "the_ai_can_be_switched_off_in_one_place" {
  variables {
    ai_enabled = false
  }

  assert {
    condition = alltrue([
      for c in jsondecode(aws_ecs_task_definition.api.container_definitions) :
      alltrue([
        for name in ["EMBEDDINGS_ENABLED", "EXTRACTION_ENABLED", "CHAT_ENABLED"] :
        anytrue([for env in c.environment : env.name == name && env.value == "false"])
      ])
    ])
    error_message = "ai_enabled = false must switch off every Bedrock call, so nothing can spend."
  }
}

run "fetching_from_outside_can_be_switched_off_in_one_place" {
  variables {
    data_sources_enabled = false
  }

  assert {
    condition = alltrue([
      for c in jsondecode(aws_ecs_task_definition.api.container_definitions) :
      anytrue([for env in c.environment : env.name == "FILINGS_DISCOVERY" && env.value == "false"]) &&
      anytrue([for env in c.environment : env.name == "PRICES_ENABLED" && env.value == "false"]) &&
      anytrue([for env in c.environment : env.name == "FEED_MODE" && env.value == "fixture"])
    ])
    error_message = "data_sources_enabled = false must stop every fetch from BSE, screener.in and RBI."
  }
}

run "a_cap_above_ten_dollars_is_refused" {
  command = plan

  variables {
    chat_budget_usd = 50
  }

  expect_failures = [var.chat_budget_usd]
}

run "a_negative_cap_is_refused" {
  command = plan

  variables {
    extraction_budget_usd = -1
  }

  expect_failures = [var.extraction_budget_usd]
}

# --- invite only (the owner, 2026-10-07) -------------------------------------------------------------

run "only_the_listed_google_accounts_may_sign_in" {
  # Google lets ANY account through when an app asks only for basic sign-in, even in its
  # "Testing" mode, so the app keeps its own list. The api is the one that signs people in.
  assert {
    condition = anytrue([
      for env in jsondecode(aws_ecs_task_definition.api.container_definitions)[0].environment :
      env.name == "ALLOWED_EMAILS" && env.value == "owner@example.com,friend@example.org"
    ])
    error_message = "The api container must receive ALLOWED_EMAILS from var.allowed_emails."
  }

  assert {
    condition = !anytrue([
      for env in jsondecode(aws_ecs_task_definition.api.container_definitions)[1].environment :
      env.name == "ALLOWED_EMAILS"
    ])
    error_message = "The worker signs nobody in; it does not need the list."
  }
}

run "an_empty_allow_list_is_refused" {
  command = plan

  # An empty list would mean "everyone": never on AWS.
  variables {
    allowed_emails = ""
  }

  expect_failures = [var.allowed_emails]
}

run "a_malformed_email_in_the_list_is_refused" {
  command = plan

  variables {
    allowed_emails = "owner@example.com,not-an-email"
  }

  expect_failures = [var.allowed_emails]
}
