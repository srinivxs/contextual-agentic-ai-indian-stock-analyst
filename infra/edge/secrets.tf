# infra/edge/secrets.tf
#
# The two values this root publishes for the application stack to read.
#
# The direction matters. These live here, in the half that is never destroyed, so the stack's data
# sources can always find them. The stack is rebuilt from nothing every night; if the secret lived
# there, CloudFront and the ALB would disagree about it after every rebuild.

# --- the shared origin secret --------------------------------------------------------------------
#
# CloudFront sends this as the X-Origin-Verify header; the ALB listener rule forwards only requests
# that carry it, and returns 403 otherwise.
#
# STATEFUL, NOT EPHEMERAL, and that is deliberate. An ephemeral random_password is regenerated on
# every operation, which would change the header and the parameter on every single apply and leave
# the two halves permanently out of step. A value that both a listener rule and an origin header
# must agree on is ordinary configuration, so it lives in state. It is defence in depth behind the
# security group's CloudFront prefix list, not the thing standing between the internet and the API.
resource "random_password" "origin_verify" {
  length = 40

  # Letters and digits only: this value travels in an HTTP header, where punctuation would have to
  # be escaped and quoting mistakes are easy to make and hard to see.
  special = false
}

locals {
  # Raise this to rotate the secret: change it, apply this root, then re-apply the stack so the
  # listener rule picks up the new value. Write-only attributes are not stored, so Terraform cannot
  # detect a change on its own -- this number is how it is told.
  origin_verify_version = 1
}

resource "aws_ssm_parameter" "origin_verify" {
  name        = local.origin_verify_parameter_name
  description = "Shared secret CloudFront sends to the load balancer as X-Origin-Verify"

  # SecureString encrypts at rest with the AWS-managed SSM key, which costs nothing. Standard
  # parameters are free.
  type = "SecureString"

  # value_wo is transmitted and then discarded, so it never reaches the state file. The generated
  # value is already in this root's state via random_password above -- see the note there -- but
  # there is no reason to keep a second copy of it.
  value_wo         = random_password.origin_verify.result
  value_wo_version = local.origin_verify_version

  tags = {
    Name = "${local.name_prefix}-origin-verify"
  }
}

# --- the public URL of the whole application -------------------------------------------------------
#
# The backend refuses to start with APP_ENV=production unless PUBLIC_BASE_URL is https, and uses it
# to build the Google OAuth redirect URI and to check the Origin header. This parameter is where the
# stack reads it from, which is why the stack needs no hand-maintained copy of the domain.
resource "aws_ssm_parameter" "public_base_url" {
  name        = local.public_base_url_parameter_name
  description = "The public HTTPS origin of the application: this distribution"

  # A public URL is not a secret. SecureString would add a KMS call at container start for nothing.
  type  = "String"
  value = "https://${aws_cloudfront_distribution.main.domain_name}"

  tags = {
    Name = "${local.name_prefix}-public-base-url"
  }
}
