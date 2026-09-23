# infra/cicd/github.tf
#
# How GitHub Actions proves who it is to AWS, and the one thing it may then do: push images.
#
# THE FLOW, END TO END
#   1. A workflow job asks GitHub for an OIDC token. GitHub signs it; among its claims are
#        iss  https://token.actions.githubusercontent.com   who minted it
#        aud  sts.amazonaws.com                             who it is meant for
#        sub  repo:<owner>/<name>:ref:refs/heads/main       which repository and which ref
#   2. The job calls sts:AssumeRoleWithWebIdentity with that token.
#   3. AWS checks the signature against the provider below, then the role's trust policy checks aud
#      and sub. Only if both match does STS return credentials, and they expire within the hour.
#
#   Nothing is stored in GitHub. There is no key to leak, rotate or forget.

# --- the identity provider ---------------------------------------------------------------------
#
# One per account for this issuer: AWS refuses a second provider with the same URL. If the account
# already has one (created by hand in the console, say), the apply fails with EntityAlreadyExists
# and the existing one has to be imported instead. The pre-apply brief checks for it.
#
# No thumbprint_list. AWS validates GitHub's certificate against its own trusted roots, and a pinned
# thumbprint would only break the day GitHub rotates its certificate.
resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  # The audience. The official aws-actions/configure-aws-credentials action requests exactly this.
  client_id_list = ["sts.amazonaws.com"]

  tags = {
    Name = "${local.name_prefix}-github-oidc"
  }
}

# --- the role GitHub assumes on every push to main ------------------------------------------------

# The subject GitHub puts in this repository's tokens. It is the IMMUTABLE form, with numeric ids
# (variables.tf explains why that is safer). P8a expected the older repo:<owner>/<name>:ref:... form;
# the first real run was refused, and `gh api repos/<owner>/<name>/actions/oidc/customization/sub`
# showed "use_immutable_subject": true. Both forms fail closed when they do not match.
locals {
  github_owner   = split("/", var.github_repository)[0]
  github_name    = split("/", var.github_repository)[1]
  github_subject = "repo:${local.github_owner}@${var.github_owner_id}/${local.github_name}@${var.github_repository_id}:ref:refs/heads/main"

  # Who may assume a pipeline role: GitHub Actions, for this repository, on main. Both roles use
  # exactly this policy (the push role here, the deploy role in deploy.tf); what differs is only what
  # each may DO once assumed. Written with jsonencode rather than aws_iam_policy_document, as in the
  # other roots: the policy is visible exactly as AWS receives it, and a mocked data source would
  # return invented JSON that the offline tests could not inspect.
  github_main_trust_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "GitHubActionsOnMainOfThisRepository"
        Effect    = "Allow"
        Action    = "sts:AssumeRoleWithWebIdentity"
        Principal = { Federated = aws_iam_openid_connect_provider.github.arn }
        Condition = {
          StringEquals = {
            # The token was minted for AWS STS, not for another service that trusts GitHub.
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"

            # THE condition that matters. Without it every repository on GitHub, a fork of this
            # one included, could assume the role, because they all get tokens from one issuer.
            #
            # StringEquals, not StringLike, so no stray character can widen the match. The ref is
            # pinned to main: a workflow on any other branch is refused. If a job ever declares a
            # GitHub `environment:`, the subject ends in :environment:<name> instead and this stops
            # matching, which fails closed. The subject's shape is explained at local.github_subject.
            "token.actions.githubusercontent.com:sub" = local.github_subject
          }
        }
      },
    ]
  })
}

resource "aws_iam_role" "ci" {
  name        = "${local.name_prefix}-ci"
  description = "Assumed by GitHub Actions on main of one repository; may only push images"

  assume_role_policy = local.github_main_trust_policy

  # One hour, the default. A build and push takes a few minutes.
  max_session_duration = 3600

  tags = {
    Name = "${local.name_prefix}-ci"
  }
}

# What the role may do once assumed: push to this one repository, and nothing else.
#
# Pushing an image and changing a running system are different powers, so they are different
# roles. This one is used on every push to main, so it gets the smaller power. The role that deploys
# is in deploy.tf, written against what the deploy job actually does.
resource "aws_iam_role_policy" "ci_push_images" {
  name = "push-backend-images"
  role = aws_iam_role.ci.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # `docker login` needs a registry token. AWS does not allow this action to be scoped to a
        # repository (it accepts only a wildcard resource), and the token alone grants nothing:
        # every push still needs the repository-scoped actions below.
        Sid      = "LogInToTheRegistry"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        # The layer upload protocol `docker push` speaks, plus the two reads it uses to see which
        # layers and manifests the repository already holds.
        Sid    = "PushToThisRepositoryOnly"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage",
          "ecr:BatchGetImage",
          "ecr:GetDownloadUrlForLayer",
        ]
        Resource = aws_ecr_repository.backend.arn
      },
    ]
  })
}
