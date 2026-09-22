# infra/stack/edge.tf
#
# The seam between this root and infra/edge. One file, so the dependency between the two halves of
# the deployment is visible in a directory listing rather than buried in whichever resource happens
# to use it.
#
# WHY THESE COME FROM SSM AND NOT FROM A VARIABLE OR A REMOTE STATE
#   infra/edge is never destroyed, so these two parameters always exist and this root can never fail
#   for want of them. That is the whole reason the wiring runs in this direction:
#
#     persistent -> ephemeral   through SSM      (these two values)
#     ephemeral -> persistent   through a flag   (the ALB's name, passed to the edge on apply)
#
#   A `terraform_remote_state` data source would work too, but it would couple this root to the
#   other's state file layout, and it would read far more than the two values it needs.
#
# CONSEQUENCE, AND IT IS INTENDED
#   A plan of this root FAILS if infra/edge has never been applied. The edge is the permanent half:
#   if it is missing, something is wrong and stopping is the right answer.

# The shared secret CloudFront sends as the X-Origin-Verify header and the listener rule matches
# on. It lives in the edge because both ends must agree on it across a rebuild of this root:
# if this root minted its own, every API request after a rebuild would get the listener's default
# 403 until CloudFront was updated too.
data "aws_ssm_parameter" "origin_verify" {
  name = local.origin_verify_parameter_name
}

# The origin the browser sees: https://<distribution>.cloudfront.net. The load balancer's own name
# cannot serve this purpose -- it is http, and it changes every time this root is rebuilt.
data "aws_ssm_parameter" "public_base_url" {
  name = local.public_base_url_parameter_name
}

locals {
  # Fixed names, written by infra/edge. Changing either means changing both roots.
  origin_verify_parameter_name   = "/stock-analyst/demo/origin_verify"
  public_base_url_parameter_name = "/stock-analyst/demo/public_base_url"

  # The provider marks EVERY SSM parameter value sensitive, including a plain String one. Left as
  # is, this value would make the whole api container definition sensitive and hide it from plan
  # output, costing reviewability for nothing: a public URL is not a secret. nonsensitive() says so
  # deliberately and in one place.
  #
  # The origin secret above is NOT unwrapped: it stays sensitive, which is why the listener rule's
  # condition prints as hidden in a plan.
  public_base_url = nonsensitive(data.aws_ssm_parameter.public_base_url.value)
}
