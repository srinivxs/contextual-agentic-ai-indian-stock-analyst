# infra/edge/variables.tf

variable "region" {
  type        = string
  description = "The one region this project uses. Only Mumbai is accepted."
  default     = "ap-south-1"

  # A typo here would build a second copy of everything somewhere else and bill for it. The
  # validation makes that impossible rather than merely unlikely.
  validation {
    condition     = var.region == "ap-south-1"
    error_message = "This project runs only in ap-south-1 (Mumbai)."
  }
}

variable "allowed_account_id" {
  type        = string
  description = "The 12-digit AWS account this root may touch. Supplied as TF_VAR_allowed_account_id."

  validation {
    condition     = can(regex("^[0-9]{12}$", var.allowed_account_id))
    error_message = "An AWS account id is exactly 12 digits."
  }
}

variable "alb_origin_domain" {
  type        = string
  description = "DNS name of the application load balancer CloudFront should send /api/* to."

  # THE ONE VALUE THAT TRAVELS EPHEMERAL -> PERSISTENT.
  #
  # The load balancer is destroyed every night and gets a new name each time it is rebuilt, so this
  # cannot be a data source: a data source pointing at something that does not exist fails the plan.
  # It arrives on the command line instead, after the application stack applies:
  #
  #   terraform apply -var alb_origin_domain=$(cd ../stack && terraform output -raw alb_dns_name)
  #
  # The default is a name that deliberately cannot resolve (.invalid is reserved by RFC 2606), which
  # is what lets this root be applied FIRST, before any application exists. While it is in force,
  # /api/* returns an error and the static site serves normally -- which is exactly the truth.
  default = "origin-not-deployed.invalid"

  validation {
    condition     = length(var.alb_origin_domain) > 0
    error_message = "An origin domain is required; leave the default to stand the edge up on its own."
  }
}

variable "price_class" {
  type        = string
  description = "Which CloudFront edge locations to use."

  # PriceClass_100 is cheapest but covers only North America and Europe -- it does NOT include
  # India, where this is demonstrated. PriceClass_200 adds India and the rest of Asia. PriceClass_All
  # adds South America, Australia and New Zealand for more money and no benefit here.
  default = "PriceClass_200"

  validation {
    condition     = contains(["PriceClass_100", "PriceClass_200"], var.price_class)
    error_message = "Use PriceClass_200 (includes India) or PriceClass_100; PriceClass_All is never worth it here."
  }
}
