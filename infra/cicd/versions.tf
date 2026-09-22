# infra/cicd: the build machinery. Applied once and kept, like the bootstrap and the edge.
#
# WHY THIS ROOT EXISTS
#   It holds the two things continuous integration needs, both of which must outlive the nightly
#   destroy of infra/stack:
#
#     the image registry   GitHub builds a container image on every push to main. If the registry
#                          were destroyed with the application -- as it was until P8 -- there would
#                          be nowhere to put that image for the 23 hours a day the stack is down.
#     the GitHub identity  an OpenID Connect provider and a role GitHub Actions may assume.
#
#   Neither has an hourly rate. ECR storage is $0.10 per GB-month and the lifecycle policy keeps five
#   images of about 71 MB, so this root costs a few cents a month. The IAM objects are free.
#
#   It is separate from infra/edge rather than folded into it because they do unrelated jobs. The
#   edge is the front door a browser talks to; this is machinery no visitor ever touches. Sharing a
#   root would save one state file and cost the ability to say what either root is for in one
#   sentence.
#
# WHY OIDC AND NOT AN ACCESS KEY
#   The alternative is an IAM user with a long-lived access key pasted into GitHub's secrets. That
#   key works until someone notices it has leaked. With OIDC, GitHub mints a short-lived token that
#   states which repository and which ref it came from; AWS verifies the signature and the conditions
#   and returns credentials that expire within the hour. Nothing is stored in GitHub, so there is
#   nothing to leak, and another repository -- or a fork of this one -- cannot assume the role,
#   because the token says where it came from and the trust policy checks.
#
# WHAT THIS ROOT DELIBERATELY DOES NOT CONTAIN
#   The role that deploys. Pushing an image and changing a running system are different powers, so
#   they are different roles: the one used on every push can only write to the registry. The deploy
#   role arrives in P8c, written against what the deploy workflow actually does rather than what it
#   might.
#
# The `backend` block is in backend.tf. The offline tests need no state and no AWS:
#   terraform init -backend=false
terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.65"
    }
  }
}
