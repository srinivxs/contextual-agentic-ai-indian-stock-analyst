# infra/stack/identity.tf
#
# Who is allowed to do what. Three roles, and one boundary that matters.
#
# TWO KINDS OF ROLE, which ECS keeps separate and people routinely confuse:
#
#   EXECUTION ROLE  used by the ECS agent BEFORE the container runs: pull the image from ECR, fetch
#                   secrets from SSM and inject them as environment variables, create log streams.
#   TASK ROLE       used by the application's own code AT RUNTIME. This is the identity boto3 picks
#                   up inside the container when it calls Bedrock.
#
# Different actors, different moments. Granting the application the execution role's permissions, or
# the reverse, is the usual way this goes wrong.
#
# THE BOUNDARY: there are two execution roles, not one. ADR 011 says the runtime database role has
# no DDL rights, and that promise is empty if the API container can read the master user's
# connection URL out of SSM and connect as it instead. So api_execution is granted the runtime
# parameter and nothing else, and the admin parameter is reachable only by migrate_execution.
# Enforced by IAM, not by anyone remembering.
#
# Nothing here costs money. IAM roles and policies are free.

# The AWS-managed key that encrypts SecureString parameters. Reading its ARN lets the policies below
# name the exact key rather than allowing decryption with any key in the account.
data "aws_kms_alias" "ssm" {
  name = "alias/aws/ssm"
}

locals {
  # All three roles are assumable only by the ECS tasks service: not by a user, not by another
  # account, not by any other AWS service.
  ecs_tasks_assume_role = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = "sts:AssumeRole"
        Principal = { Service = "ecs-tasks.amazonaws.com" }
      },
    ]
  })

  # Both execution roles need this: it is what lets the ECS agent pull from ECR and write logs.
  ecs_execution_managed_policy = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# --- the API's execution role -----------------------------------------------------------------------

resource "aws_iam_role" "api_execution" {
  name               = "${local.name_prefix}-api-execution"
  description        = "ECS pulls the image and injects the runtime database URL as this role"
  assume_role_policy = local.ecs_tasks_assume_role

  tags = {
    Name = "${local.name_prefix}-api-execution"
  }
}

resource "aws_iam_role_policy_attachment" "api_execution_managed" {
  role       = aws_iam_role.api_execution.name
  policy_arn = local.ecs_execution_managed_policy
}

# One parameter, named exactly. The admin URL is absent, and that absence is the security control.
resource "aws_iam_role_policy" "api_execution_secrets" {
  name = "read-runtime-database-url"
  role = aws_iam_role.api_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # The runtime database URL and the two application secrets: everything the API container
        # needs to start, and nothing else. The admin URL is deliberately absent.
        Sid    = "ReadRuntimeSecrets"
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = [
          local.runtime_parameter_arn,
          local.session_secret_arn,
          local.google_client_secret_arn,
        ]
      },
      {
        # A SecureString cannot be read without also decrypting it.
        Sid      = "DecryptSecureStringParameters"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [data.aws_kms_alias.ssm.target_key_arn]
      },
    ]
  })
}

# --- the migration's execution role -------------------------------------------------------------------

resource "aws_iam_role" "migrate_execution" {
  name               = "${local.name_prefix}-migrate-execution"
  description        = "ECS injects both database URLs into the one-off migration task as this role"
  assume_role_policy = local.ecs_tasks_assume_role

  tags = {
    Name = "${local.name_prefix}-migrate-execution"
  }
}

resource "aws_iam_role_policy_attachment" "migrate_execution_managed" {
  role       = aws_iam_role.migrate_execution.name
  policy_arn = local.ecs_execution_managed_policy
}

# Both parameters. The admin URL is how the migration connects; the runtime URL is how it learns the
# password to give the no-DDL role it creates inside PostgreSQL.
resource "aws_iam_role_policy" "migrate_execution_secrets" {
  name = "read-both-database-urls"
  role = aws_iam_role.migrate_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadBothDatabaseUrls"
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParameters"]
        Resource = [
          local.migration_parameter_arn,
          local.runtime_parameter_arn,
        ]
      },
      {
        Sid      = "DecryptSecureStringParameters"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [data.aws_kms_alias.ssm.target_key_arn]
      },
    ]
  })
}

# --- the application's own role ------------------------------------------------------------------------
#
# The identity boto3 picks up inside the api and worker containers. Since go-live it may do exactly
# two things: keep filings in the documents bucket, and call the two Bedrock models (both below).
# It never gets database or secret permissions: the application receives its connection URL as an
# injected environment variable and has no reason to talk to SSM itself.
resource "aws_iam_role" "task" {
  name               = "${local.name_prefix}-task"
  description        = "The identity the application code runs as inside the container"
  assume_role_policy = local.ecs_tasks_assume_role

  tags = {
    Name = "${local.name_prefix}-task"
  }
}

# Filings: put and get, only under documents/ in this stack's bucket. No list, no delete: every key
# is built from the file's SHA-256, so the code never needs to look around or remove anything.
resource "aws_iam_role_policy" "task_documents" {
  name = "keep-filings-in-the-documents-bucket"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadAndWriteFilings"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.documents.arn}/documents/*"
      },
    ]
  })
}

locals {
  # ADR 010. Titan V2 runs in Mumbai. Nova 2 Lite is available here only through the GLOBAL
  # inference profile, which may serve a request from any supported region.
  titan_model_arn     = "arn:aws:bedrock:${var.region}::foundation-model/amazon.titan-embed-text-v2:0"
  nova_profile_arn    = "arn:aws:bedrock:${var.region}:${var.allowed_account_id}:inference-profile/global.amazon.nova-2-lite-v1:0"
  nova_regional_model = "arn:aws:bedrock:${var.region}::foundation-model/amazon.nova-2-lite-v1:0"
  nova_global_model   = "arn:aws:bedrock:::foundation-model/amazon.nova-2-lite-v1:0"
}

# Bedrock: InvokeModel only (the Converse API the chat and extraction use is authorised by it).
# The shape ADR 010 recorded for a global profile: the profile itself, plus the source-region and
# the regionless global model, each allowed only when the request comes through that profile.
resource "aws_iam_role_policy" "task_bedrock" {
  name = "call-titan-and-nova"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EmbedWithTitan"
        Effect   = "Allow"
        Action   = "bedrock:InvokeModel"
        Resource = local.titan_model_arn
      },
      {
        Sid      = "NovaThroughTheGlobalProfile"
        Effect   = "Allow"
        Action   = "bedrock:InvokeModel"
        Resource = local.nova_profile_arn
      },
      {
        Sid      = "NovaInTheSourceRegion"
        Effect   = "Allow"
        Action   = "bedrock:InvokeModel"
        Resource = local.nova_regional_model
        Condition = {
          StringEquals = {
            "bedrock:InferenceProfileArn" = local.nova_profile_arn
            "aws:RequestedRegion"         = var.region
          }
        }
      },
      {
        Sid      = "NovaAnywhereTheProfileRoutes"
        Effect   = "Allow"
        Action   = "bedrock:InvokeModel"
        Resource = local.nova_global_model
        Condition = {
          StringEquals = {
            "bedrock:InferenceProfileArn" = local.nova_profile_arn
            "aws:RequestedRegion"         = "unspecified"
          }
        }
      },
    ]
  })
}

