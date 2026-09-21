# infra/stack/main.tf
#
# The wiring shared by every file in this root module: which AWS we talk to, and the two values
# that name and tag everything. The resources themselves live in network.tf and security.tf.

# --- who Terraform is talking to -----------------------------------------------------------------
#
# The same two guards as the other roots, for the same reasons:
#   region              taken from a variable that accepts only Mumbai, so it always wins over any
#                       AWS_REGION left set in a terminal.
#   allowed_account_ids the provider asks AWS which account the credentials belong to and refuses to
#                       continue if it is not this one. The cost gate, enforced in code.
#
# default_tags puts the three project tags on every resource this provider creates, so the
# "did I leave anything running?" audit can find them all by searching one tag.
provider "aws" {
  region              = var.region
  allowed_account_ids = [var.allowed_account_id]

  default_tags {
    tags = local.default_tags
  }
}

locals {
  # Prefixed onto every Name tag, so that in the console it is obvious at a glance which resources
  # belong to this project and which were already in the account.
  name_prefix = "stock-analyst-demo"

  # Project is what the post-destroy audit searches for.
  default_tags = {
    Project     = "stock-analyst"
    Environment = "demo"
    ManagedBy   = "terraform"
  }
}
