# infra/bootstrap/variables.tf
#
# The two inputs this folder takes. Both are validated: a variable with a `validation` block fails
# before Terraform contacts AWS at all, which is the cheapest possible place to catch a mistake.

variable "region" {
  type        = string
  description = "The AWS region. Only ap-south-1 (Mumbai) is accepted."
  default     = "ap-south-1"

  # Not a free choice, a documented constant with a guard. The whole project lives in one region
  # (ADR 010), and a state bucket created in the wrong one would be a quiet, annoying mistake:
  # everything would still work, and the bill would appear in a region nobody looks at.
  validation {
    condition     = var.region == "ap-south-1"
    error_message = "Only ap-south-1 (Mumbai) is allowed: the whole project lives in one region."
  }
}

variable "allowed_account_id" {
  type        = string
  description = "The 12-digit AWS account ID Terraform is allowed to work in."

  # No default, on purpose. The account id is not written in any file in this repository; it is
  # supplied from the environment as TF_VAR_allowed_account_id. Leaving it out is then an error
  # rather than a silent fallback to somebody else's account.
  validation {
    condition     = can(regex("^[0-9]{12}$", var.allowed_account_id))
    error_message = "allowed_account_id must be exactly 12 digits."
  }
}
