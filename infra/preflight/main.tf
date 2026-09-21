# infra/preflight/main.tf
#
# A READ-ONLY check that Terraform can use our AWS credentials. It creates nothing: it contains
# no `resource` blocks at all, only two data sources (lookups).
#
# Run it with `terraform test` (see tests/credentials.tftest.hcl), or `terraform plan` to see the
# masked outputs. No `apply` is needed, because there is nothing to create.

# --- input -----------------------------------------------------------------------------------
#
# The account we intend to work in. There is no default on purpose, and the value is never written
# in a file: it arrives from the environment as TF_VAR_allowed_account_id, so the account number
# stays out of Git.
variable "allowed_account_id" {
  type        = string
  description = "The 12-digit AWS account ID Terraform is allowed to work in."

  validation {
    condition     = can(regex("^[0-9]{12}$", var.allowed_account_id))
    error_message = "allowed_account_id must be exactly 12 digits."
  }
}

# --- the provider ---------------------------------------------------------------------------------
#
# A "provider" is the plugin that talks to one system (here, AWS). Two guards are set here:
#
#   region              Written literally, so it always wins over any AWS_REGION variable that might
#                       be lying around in a terminal. Everything in this project lives in Mumbai.
#   allowed_account_ids After the credentials are resolved, the provider asks AWS which account they
#                       belong to and REFUSES TO CONTINUE if it is not this one. It is the cost
#                       gate "am I in the right account?", enforced in code rather than by habit.
#
# There are no credentials here. The provider finds them the normal way (the AWS_PROFILE
# environment variable, or the AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN
# variables), which is exactly what this preflight exists to test.
provider "aws" {
  region              = "ap-south-1"
  allowed_account_ids = [var.allowed_account_id]
}

# --- the two lookups -----------------------------------------------------------------------------------
#
# A "data source" reads something that already exists. It cannot create or change anything.

# Who am I, according to AWS? (arn, account_id, user_id)
data "aws_caller_identity" "current" {}

# Which region is the provider using?
data "aws_region" "current" {}

# --- a value derived for the outputs ---------------------------------------------------------------
#
# The ARN of an IAM user looks like arn:aws:iam::<12-digit account>:user/<name>. Splitting it on "/"
# and taking the last piece gives just the user name, so we never have to print the account number.
locals {
  arn_parts     = split("/", data.aws_caller_identity.current.arn)
  identity_name = local.arn_parts[length(local.arn_parts) - 1]
}
