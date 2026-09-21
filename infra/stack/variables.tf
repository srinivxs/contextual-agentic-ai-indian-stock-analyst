# infra/stack/variables.tf
#
# The three inputs this root module takes. Each has a `validation` block, which Terraform checks
# before it contacts AWS at all: the cheapest possible place to catch a mistake.

variable "region" {
  type        = string
  description = "The AWS region. Only ap-south-1 (Mumbai) is accepted."
  default     = "ap-south-1"

  validation {
    condition     = var.region == "ap-south-1"
    error_message = "Only ap-south-1 (Mumbai) is allowed: the whole project lives in one region."
  }
}

variable "allowed_account_id" {
  type        = string
  description = "The 12-digit AWS account ID Terraform is allowed to work in."

  # No default. The account ID is never written in this repository; it is supplied from the
  # environment as TF_VAR_allowed_account_id, so leaving it out is an error rather than a silent
  # fallback into some other account.
  validation {
    condition     = can(regex("^[0-9]{12}$", var.allowed_account_id))
    error_message = "allowed_account_id must be exactly 12 digits."
  }
}

variable "vpc_cidr" {
  type        = string
  description = "The private address range for the VPC. Must be a /16."
  default     = "10.0.0.0/16"

  # A /16 is required, not merely preferred: network.tf cuts this range into /24 subnets by adding
  # 8 bits. Give it a /28 and that arithmetic fails much later with a far less obvious message, so
  # the variable refuses it here.
  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0)) && can(regex("/16$", var.vpc_cidr))
    error_message = "vpc_cidr must be a valid CIDR block ending in /16, for example 10.0.0.0/16."
  }
}
