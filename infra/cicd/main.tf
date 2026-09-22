# infra/cicd/main.tf
#
# The wiring shared by every file in this root: which AWS we talk to, and the names everything else
# derives from. The resources live in registry.tf and github.tf.

# --- who Terraform is talking to -----------------------------------------------------------------
#
# The same two guards as the other roots, for the same reasons:
#   region              from a variable that accepts only Mumbai, so it always wins over any
#                       AWS_REGION left set in a terminal.
#   allowed_account_ids the provider asks AWS which account the credentials belong to and refuses to
#                       continue if it is not this one. The cost gate, enforced in code.
provider "aws" {
  region              = var.region
  allowed_account_ids = [var.allowed_account_id]

  default_tags {
    tags = local.default_tags
  }
}

locals {
  # The same prefix as the other roots, so the console and the post-destroy audit show one project.
  name_prefix = "stock-analyst-demo"

  default_tags = {
    Project     = "stock-analyst"
    Environment = "demo"
    ManagedBy   = "terraform"
  }

  # The one value the application stack reads from this root. A fixed name, because the stack's
  # data source names it exactly (persistent -> ephemeral through SSM, as with infra/edge).
  ecr_repository_url_parameter_name = "/stock-analyst/demo/ecr_repository_url"
}
