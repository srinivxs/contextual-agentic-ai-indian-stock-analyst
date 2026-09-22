# infra/edge/main.tf
#
# The wiring shared by every file in this root: which AWS we talk to, and the names everything else
# derives from. The resources live in frontend.tf, cloudfront.tf and secrets.tf.

# --- who Terraform is talking to -----------------------------------------------------------------
#
# The same two guards as the other roots, for the same reasons:
#   region              from a variable that accepts only Mumbai, so it always wins over any
#                       AWS_REGION left set in a terminal.
#   allowed_account_ids the provider asks AWS which account the credentials belong to and refuses to
#                       continue if it is not this one. The cost gate, enforced in code.
#
# CloudFront itself is a global service with a single global endpoint, so the region below governs
# the S3 bucket and the SSM parameters rather than the distribution. (A custom domain would need an
# ACM certificate in us-east-1; this project uses the default *.cloudfront.net certificate, so no
# certificate is involved and no second provider is needed.)
provider "aws" {
  region              = var.region
  allowed_account_ids = [var.allowed_account_id]

  default_tags {
    tags = local.default_tags
  }
}

locals {
  # The same prefix as the application stack, so the post-destroy audit and the console both show
  # one project rather than two.
  name_prefix = "stock-analyst-demo"

  default_tags = {
    Project     = "stock-analyst"
    Environment = "demo"
    ManagedBy   = "terraform"
  }

  # S3 bucket names are globally unique across all of AWS, so the account id is part of the name --
  # the same trick the bootstrap uses for the state bucket.
  site_bucket_name = "${local.name_prefix}-site-${var.allowed_account_id}"

  # Origin ids are internal labels: the cache behaviours name an origin by one of these strings.
  # They are not DNS names and never appear outside the distribution's own configuration.
  s3_origin_id  = "site"
  alb_origin_id = "api"

  # The two values the application stack reads. Fixed names, because the stack's data sources name
  # them exactly (persistent -> ephemeral through SSM; see versions.tf).
  origin_verify_parameter_name   = "/stock-analyst/demo/origin_verify"
  public_base_url_parameter_name = "/stock-analyst/demo/public_base_url"
}
