# infra/cicd/deploy.tf
#
# The role the pipeline's deploy job assumes (P8c, ADR 017). Same trust as the push role in
# github.tf -- this repository, main only -- but a different power: it changes a running system.
#
# WHAT THE DEPLOY JOB DOES, AND SO ALL THIS ROLE MAY DO
#   .github/scripts/deploy_backend.py, while the stack is up:
#     1. describe the api service             is the stack up? which network does it use?
#     2. describe + register task definitions  new api and migrate revisions carrying the new image
#     3. run the migration task, wait, read its exit code
#     4. update the api service                ECS rolls it out; the circuit breaker rolls back
#   the frontend step, always:
#     5. sync the static export to the site bucket and invalidate the distribution's cache
#
# Every ARN below is written out, so the role cannot reach anything the list above does not name.
# Two actions cannot be scoped: AWS accepts only "*" as the resource of DescribeTaskDefinition and
# RegisterTaskDefinition. Registering a definition runs nothing, and the actions that DO run
# something (RunTask, UpdateService) and the roles a definition may carry (PassRole) are all scoped.
#
# The ECS names are infra/stack's. That root is destroyed nightly, so it cannot be read here (a data
# source must never point at something that may not exist); the names are fixed there, and while
# the stack is down these permissions simply match nothing.

# Published by infra/edge, which is permanent, so these always exist.
data "aws_ssm_parameter" "site_bucket_name" {
  name = "/stock-analyst/demo/site_bucket_name"
}

data "aws_ssm_parameter" "distribution_id" {
  name = "/stock-analyst/demo/distribution_id"
}

locals {
  ecs_arn_prefix = "arn:aws:ecs:${var.region}:${var.allowed_account_id}"
  ecs_cluster    = local.name_prefix
  site_bucket    = nonsensitive(data.aws_ssm_parameter.site_bucket_name.value)
  distribution   = nonsensitive(data.aws_ssm_parameter.distribution_id.value)

  # The values the deploy job reads at run time. Never the origin secret, the Google secret or the
  # database URLs, although they sit under the same prefix.
  deploy_readable_parameters = [
    "site_bucket_name",
    "distribution_id",
    "public_base_url",
    "ecr_repository_url",
  ]
}

resource "aws_iam_role" "deploy" {
  name        = "${local.name_prefix}-deploy"
  description = "Assumed by GitHub Actions on main of one repository; deploys the pushed image"

  assume_role_policy = local.github_main_trust_policy

  # One hour. A deploy is a migration (about a minute) plus a rollout (a few minutes).
  max_session_duration = 3600

  tags = {
    Name = "${local.name_prefix}-deploy"
  }
}

resource "aws_iam_role_policy" "deploy" {
  name = "deploy-this-project"
  role = aws_iam_role.deploy.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadWhereThingsAre"
        Effect = "Allow"
        Action = ["ssm:GetParameter"]
        Resource = [
          for name in local.deploy_readable_parameters :
          "arn:aws:ssm:${var.region}:${var.allowed_account_id}:parameter/stock-analyst/demo/${name}"
        ]
      },
      {
        # Is the stack up, and on which network? Also what `ecs wait services-stable` polls.
        Sid      = "SeeTheApiService"
        Effect   = "Allow"
        Action   = ["ecs:DescribeServices"]
        Resource = "${local.ecs_arn_prefix}:service/${local.ecs_cluster}/${local.name_prefix}-api"
      },
      {
        Sid      = "RollOutTheApiService"
        Effect   = "Allow"
        Action   = ["ecs:UpdateService"]
        Resource = "${local.ecs_arn_prefix}:service/${local.ecs_cluster}/${local.name_prefix}-api"
      },
      {
        # Unscopable (see the top of this file). Registering runs nothing.
        Sid      = "CopyTaskDefinitionsWithANewImage"
        Effect   = "Allow"
        Action   = ["ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition"]
        Resource = "*"
      },
      {
        # The migration family only: never the provision task, which creates database roles.
        Sid      = "RunTheMigration"
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = "${local.ecs_arn_prefix}:task-definition/${local.name_prefix}-migrate:*"
        Condition = {
          ArnEquals = { "ecs:cluster" = "${local.ecs_arn_prefix}:cluster/${local.ecs_cluster}" }
        }
      },
      {
        # `ecs wait tasks-stopped`, then the exit code.
        Sid      = "WatchTheMigration"
        Effect   = "Allow"
        Action   = ["ecs:DescribeTasks"]
        Resource = "${local.ecs_arn_prefix}:task/${local.ecs_cluster}/*"
      },
      {
        # A task definition names its roles, and registering or running it hands them to ECS.
        # Only the three the stack's api and migrate definitions carry, and only to ECS tasks.
        Sid    = "HandTheTaskRolesToEcs"
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [
          for role in ["api-execution", "migrate-execution", "task"] :
          "arn:aws:iam::${var.allowed_account_id}:role/${local.name_prefix}-${role}"
        ]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      },
      {
        # `aws s3 sync --delete` lists the bucket to find what to remove.
        Sid      = "ListTheSite"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = "arn:aws:s3:::${local.site_bucket}"
      },
      {
        Sid      = "PublishTheSite"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:DeleteObject"]
        Resource = "arn:aws:s3:::${local.site_bucket}/*"
      },
      {
        Sid      = "RefreshTheCache"
        Effect   = "Allow"
        Action   = ["cloudfront:CreateInvalidation"]
        Resource = "arn:aws:cloudfront::${var.allowed_account_id}:distribution/${local.distribution}"
      },
    ]
  })
}
