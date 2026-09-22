# infra/edge: the PERSISTENT half of the deployment. Applied once; never destroyed with the app.
#
# WHY THIS ROOT EXISTS (decided 2026-09-22, see ADR 015)
#   CloudFront hands out a new *.cloudfront.net domain every time a distribution is created, and
#   that domain is baked into the Google OAuth client's redirect URI. If the distribution died with
#   the rest of the stack every night, every cold start would need a Google console change, and
#   Google warns a redirect-URI change can take anywhere from five minutes to a few hours to take
#   effect. Before a demo that is an unacceptable gamble.
#
#   So the deployment is split by LIFETIME, not by layer:
#
#     infra/bootstrap  forever    the state bucket
#     infra/edge       forever    this root: the domain, the static site, the shared origin secret
#     infra/stack      nightly    VPC, ALB, ECS, RDS, ECR, IAM -- everything that actually costs money
#
#   CloudFront has no per-distribution standing charge (verified against the AWS Price List API on
#   2026-09-22: there is no "Fee" product family for CloudFront at all). An idle distribution plus a
#   few megabytes of static files costs effectively nothing, while the half that is destroyed nightly
#   costs about $1.41 a day. Keeping this alive buys a permanent domain for approximately free.
#
# HOW THE TWO ROOTS TALK TO EACH OTHER
#   The rule is: PERSISTENT -> EPHEMERAL through SSM, EPHEMERAL -> PERSISTENT through a variable.
#   Never point a data source at something that is destroyed every night.
#
#     this root writes  /stock-analyst/demo/origin_verify    (SecureString) -- read by the stack
#                       /stock-analyst/demo/public_base_url  (String)       -- read by the stack
#     this root reads   var.alb_origin_domain -- passed on the command line after the stack applies
#
#   Both parameters are always present, because this root is never destroyed, so the stack can never
#   fail for want of them. The one value that changes on every rebuild travels the other way, as a
#   plain (non-secret) variable.
#
# There is deliberately NO `backend` block here yet, for the same reason as infra/stack: an empty
# `backend "s3" {}` makes plain `terraform init` stop and ask for settings, which is unhelpful while
# the tests are still red. backend.tf arrives with the implementation.
# Until then: terraform init -backend=false
terraform {
  # 1.11: S3-native state locking (use_lockfile) and write-only attributes. Same floor as the other
  # roots, so one Terraform build serves all of them.
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # Same pin as every other root, so one provider download serves them all.
      version = "~> 6.65"
    }

    # The X-Origin-Verify secret lives here rather than in the stack, because CloudFront and the
    # ALB listener rule must agree on it and this is the half that survives. 3.9 is the first
    # release with ephemeral random_password.
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }
}
