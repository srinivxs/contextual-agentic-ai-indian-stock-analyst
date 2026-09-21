# infra/stack: the application stack. Everything here is meant to be destroyed and recreated at will.
#
# This is the second and last root module. Unlike infra/bootstrap, it keeps its state in the S3
# bucket the bootstrap created, so the state survives even when every resource below is destroyed.
#
# There is deliberately NO `backend` block in this file yet. A `backend "s3" {}` block with no
# settings makes plain `terraform init` stop and ask for them, which is unhelpful while we are still
# writing tests. The backend is added in its own file (backend.tf) in the implementation step,
# together with backend.hcl.example, at the point where we actually want to talk to S3.
#
# Until then, initialise with `terraform init -backend=false`: providers only, no state, no AWS.
terraform {
  # 1.11: S3-native state locking (use_lockfile) became generally available, which is what lets this
  # project store state safely in S3 with no separate lock table.
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # "~> 6.65": 6.65 or newer, never 7.x. Same pin as the other roots, so one provider build
      # serves all of them.
      version = "~> 6.65"
    }
  }
}
