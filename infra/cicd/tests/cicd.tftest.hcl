# Offline tests for P8a: the image registry and the identity GitHub Actions assumes.
#
# HOW TO READ THIS FILE
#   Same shape as the other roots' tests. `mock_provider "aws"` swaps in a fake AWS with the same
#   schema that talks to nothing, so the whole root is planned against the fake.
#
# HOW TO RUN IT
#   cd infra/cicd && terraform init -backend=false && terraform test
#
# WHAT THE IMPLEMENTATION MUST CALL THINGS (the contract, written before the implementation)
#   variables   region, allowed_account_id, github_repository
#   resources   aws_ecr_repository.backend
#               aws_ecr_lifecycle_policy.backend
#               aws_ssm_parameter.ecr_repository_url
#               aws_iam_openid_connect_provider.github
#               aws_iam_role.ci
#               aws_iam_role_policy.ci_push_images
#   outputs     ecr_repository_url, ci_role_arn
#
# WHY THESE TESTS ARE RED TODAY
#   None of the names above are declared, so Terraform reports "reference to undeclared ...".
#
# WHAT THESE TESTS CANNOT PROVE
#   That GitHub can actually assume the role. The trust policy is checked here as configuration; only
#   a real workflow run proves the token's `sub` claim matches what the condition expects. That is
#   P8b's first job and its most likely failure.

mock_provider "aws" {}

# The repository's arn and url are referenced by the policy and the published parameter, and the
# provider validates arn-shaped arguments, so a mock's invented short string fails the plan.
override_resource {
  target = aws_ecr_repository.backend
  values = {
    arn            = "arn:aws:ecr:ap-south-1:123456789012:repository/stock-analyst-demo-backend"
    repository_url = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/stock-analyst-demo-backend"
  }
}

override_resource {
  target = aws_iam_openid_connect_provider.github
  values = {
    arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
  }
}

variables {
  allowed_account_id = "123456789012"
  github_repository  = "srinivxs/contextual-agentic-ai-indian-stock-analyst"
}

# --- the registry ------------------------------------------------------------------------------------

run "the_registry_outlives_the_application_and_prunes_itself" {
  # The whole reason this moved out of infra/stack: CI pushes an image on every commit, and for most
  # of any given day the application stack does not exist.
  assert {
    condition     = aws_ecr_repository.backend.force_delete == true
    error_message = "ECR refuses to delete a repository containing images; destroy would fail without this."
  }

  assert {
    condition     = aws_ecr_repository.backend.image_scanning_configuration[0].scan_on_push == true
    error_message = "Basic scanning is free and reports known vulnerabilities in the image's packages."
  }

  assert {
    condition     = strcontains(aws_ecr_lifecycle_policy.backend.policy, "imageCountMoreThan")
    error_message = "Without a lifecycle policy every image ever pushed is kept and paid for."
  }
}

# ADDED IN P8b. CI tags every image with its commit SHA, so a tag names one commit forever. IMMUTABLE
# makes ECR refuse a second push to an existing tag: what ran yesterday under a SHA is what runs today.
run "a_pushed_tag_can_never_be_overwritten" {
  assert {
    condition     = aws_ecr_repository.backend.image_tag_mutability == "IMMUTABLE"
    error_message = "Tags are commit SHAs; a second push under one must be refused, not silently replace the image."
  }
}

run "the_stack_is_told_where_to_pull_from" {
  # infra/stack no longer owns the repository, so it reads the URL the same way it reads the edge's
  # values: through a parameter that is always there, because this root is never destroyed.
  assert {
    condition     = aws_ssm_parameter.ecr_repository_url.value == aws_ecr_repository.backend.repository_url
    error_message = "The published parameter must be this repository's URL."
  }

  assert {
    condition     = aws_ssm_parameter.ecr_repository_url.type == "String"
    error_message = "A registry URL is not a secret; SecureString would cost a KMS call for nothing."
  }

  assert {
    condition     = aws_ssm_parameter.ecr_repository_url.name == "/stock-analyst/demo/ecr_repository_url"
    error_message = "infra/stack reads this exact name."
  }
}

# --- how GitHub proves who it is -------------------------------------------------------------------------

run "github_is_trusted_through_oidc_rather_than_a_stored_key" {
  assert {
    condition     = aws_iam_openid_connect_provider.github.url == "https://token.actions.githubusercontent.com"
    error_message = "This is the issuer GitHub Actions mints tokens from."
  }

  assert {
    condition     = contains(aws_iam_openid_connect_provider.github.client_id_list, "sts.amazonaws.com")
    error_message = "The audience must be sts.amazonaws.com, or a token minted for another service would be accepted."
  }
}

run "only_this_repository_on_main_may_assume_the_role" {
  # The single most important assertion in this file. A trust policy that checks the issuer but not
  # the subject would let ANY GitHub repository in the world -- including a fork of this one -- assume
  # the role and push to the registry.
  assert {
    condition = strcontains(
      aws_iam_role.ci.assume_role_policy,
      "repo:${var.github_repository}:ref:refs/heads/main"
    )
    error_message = "The trust policy must name this repository AND the main ref, not just the issuer."
  }

  assert {
    condition     = strcontains(aws_iam_role.ci.assume_role_policy, "token.actions.githubusercontent.com:aud")
    error_message = "The audience claim must be checked as well as the subject."
  }

  assert {
    condition     = !strcontains(aws_iam_role.ci.assume_role_policy, "repo:*")
    error_message = "A wildcard repository in the subject condition would trust every repository on GitHub."
  }

  assert {
    condition     = strcontains(aws_iam_role.ci.assume_role_policy, aws_iam_openid_connect_provider.github.arn)
    error_message = "The role must trust this account's provider, by ARN."
  }
}

# --- what that identity is allowed to do --------------------------------------------------------------------

run "the_push_role_cannot_touch_the_running_system" {
  # Pushing an image and changing a running system are different powers. This role is assumed on
  # every push to main, so it gets the smaller one; the deploy role arrives separately in P8c.
  assert {
    condition = alltrue([
      for service in ["ecs:", "s3:", "cloudfront:", "rds:", "iam:", "ssm:"] :
      !strcontains(aws_iam_role_policy.ci_push_images.policy, service)
    ])
    error_message = "The push role must grant ECR actions only."
  }
}

run "the_push_role_is_scoped_to_this_repository" {
  assert {
    condition     = strcontains(aws_iam_role_policy.ci_push_images.policy, aws_ecr_repository.backend.arn)
    error_message = "The push actions must name this repository, not every repository in the account."
  }

  # ecr:GetAuthorizationToken is the one action AWS does not allow to be scoped: it returns a
  # registry-wide token and only accepts Resource "*". Everything else must name the repository, so
  # this asserts the exception is the ONLY wildcard in the policy.
  assert {
    condition     = length(regexall("\"\\*\"", aws_iam_role_policy.ci_push_images.policy)) == 1
    error_message = "Exactly one wildcard resource is allowed, for ecr:GetAuthorizationToken."
  }

  assert {
    condition     = strcontains(aws_iam_role_policy.ci_push_images.policy, "ecr:GetAuthorizationToken")
    error_message = "docker login cannot work without it."
  }
}

run "the_outputs_name_what_the_workflow_needs" {
  assert {
    condition     = output.ci_role_arn == aws_iam_role.ci.arn
    error_message = "The workflow needs the role ARN to assume it."
  }

  assert {
    condition     = output.ecr_repository_url == aws_ecr_repository.backend.repository_url
    error_message = "The workflow needs somewhere to push to."
  }
}

# --- the guards that stop an expensive mistake -----------------------------------------------------------

run "any_region_other_than_mumbai_is_rejected" {
  command = plan

  variables {
    region = "us-east-1"
  }

  expect_failures = [var.region]
}

run "a_malformed_account_id_is_rejected" {
  command = plan

  variables {
    allowed_account_id = "12345"
  }

  expect_failures = [var.allowed_account_id]
}

run "a_repository_that_is_not_owner_slash_name_is_rejected" {
  command = plan

  variables {
    github_repository = "not-a-repository"
  }

  expect_failures = [var.github_repository]
}
