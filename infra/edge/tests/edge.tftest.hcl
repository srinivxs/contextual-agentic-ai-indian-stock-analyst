# Offline tests for P7e1: the persistent edge -- the private S3 site bucket, the CloudFront
# distribution in front of it, and the two SSM parameters the application stack reads.
#
# HOW TO READ THIS FILE
#   Same shape as the other roots' tests. `mock_provider "aws"` swaps in a fake AWS with the same
#   schema that talks to nothing, so the whole root module is planned against the fake. The `random`
#   provider is NOT mocked: it works locally and letting the real one run is a genuine check.
#
# HOW TO RUN IT
#   cd infra/edge && terraform init -backend=false && terraform test
#
# WHAT THE IMPLEMENTATION MUST CALL THINGS (the contract, written before the implementation)
#   variables   region, allowed_account_id, alb_origin_domain, price_class
#   data        aws_cloudfront_cache_policy.caching_disabled
#               aws_cloudfront_cache_policy.caching_optimized
#               aws_cloudfront_origin_request_policy.all_viewer_except_host
#   resources   aws_s3_bucket.site
#               aws_s3_bucket_public_access_block.site
#               aws_s3_bucket_ownership_controls.site
#               aws_s3_bucket_server_side_encryption_configuration.site
#               aws_s3_bucket_policy.site
#               aws_cloudfront_origin_access_control.site
#               aws_cloudfront_function.rewrite_index
#               aws_cloudfront_distribution.main
#               random_password.origin_verify
#               aws_ssm_parameter.origin_verify
#               aws_ssm_parameter.public_base_url
#   outputs     cloudfront_domain_name, public_base_url, site_bucket_name, distribution_id
#
# WHY THESE TESTS ARE RED TODAY
#   None of the names above are declared, so Terraform reports "reference to undeclared ...".
#
# WHAT THESE TESTS CANNOT PROVE
#   That the distribution actually deploys, how long it takes, that the default *.cloudfront.net
#   certificate serves HTTPS, that the Function's JavaScript is accepted by the CloudFront runtime,
#   or that Google accepts a cloudfront.net redirect URI. All of those need a real apply. The
#   Function's LOGIC is proven separately and offline by functions/rewrite-index.test.mjs.

mock_provider "aws" {
  mock_data "aws_cloudfront_cache_policy" {
    defaults = {
      id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    }
  }

  mock_data "aws_cloudfront_origin_request_policy" {
    defaults = {
      id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
    }
  }
}

# A LIMIT OF MOCK PROVIDERS, AND WHERE THE GUARANTEE ACTUALLY LIVES
#   Under `mock_provider` every data source of one TYPE resolves to a single generated id, so the
#   two managed cache policies share one. `override_data` was tried and does NOT take effect (a
#   probe asserting the two ids DIFFER fails). The two assertions below that compare a behaviour's
#   cache_policy_id to a data source id therefore cannot tell CachingDisabled from CachingOptimized:
#   a deliberate breakage that swapped them was not caught here.
#
#   They are kept, because they state the intent in the place a reader looks for it. The ENFORCEMENT
#   is in infra/tests/test_infra_hygiene.py -- test_the_api_behaviour_never_caches_and_forwards_the
#   _session and test_the_static_behaviour_is_the_one_that_caches -- which read cloudfront.tf as
#   text and do catch the swap. Blunt, but real. Same move P7c made for the plaintext-secret
#   attribute.

# The distribution's own arn and domain_name are referenced by the bucket policy and by the
# public_base_url parameter, and the provider validates arn-shaped arguments, so a mock's invented
# short string fails the plan. Overriding this one address keeps those assertions meaningful.
override_resource {
  target = aws_cloudfront_distribution.main
  values = {
    arn         = "arn:aws:cloudfront::123456789012:distribution/E1MOCKDISTRIB"
    id          = "E1MOCKDISTRIB"
    domain_name = "d111111abcdef8.cloudfront.net"
  }
}

override_resource {
  target = aws_cloudfront_function.rewrite_index
  values = {
    arn = "arn:aws:cloudfront::123456789012:function/stock-analyst-demo-rewrite-index"
  }
}

override_resource {
  target = aws_cloudfront_origin_access_control.site
  values = {
    id = "E2MOCKOAC"
  }
}

variables {
  allowed_account_id = "123456789012"
}

# --- the bucket nobody can read directly ------------------------------------------------------------

run "the_site_bucket_is_private_and_encrypted" {
  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.site.block_public_acls,
      aws_s3_bucket_public_access_block.site.block_public_policy,
      aws_s3_bucket_public_access_block.site.ignore_public_acls,
      aws_s3_bucket_public_access_block.site.restrict_public_buckets,
    ])
    error_message = "The site bucket must block public access every way S3 offers."
  }

  # `rule` is a SET of objects on both resources below, and set elements have no addressable index,
  # so these iterate rather than subscript. Same assertion, legal expression.
  assert {
    condition = anytrue([
      for rule in aws_s3_bucket_ownership_controls.site.rule :
      rule.object_ownership == "BucketOwnerEnforced"
    ])
    error_message = "ACLs are disabled; the bucket owner owns every object."
  }

  assert {
    condition = anytrue([
      for rule in aws_s3_bucket_server_side_encryption_configuration.site.rule :
      anytrue([
        for default in rule.apply_server_side_encryption_by_default :
        default.sse_algorithm == "AES256"
      ])
    ])
    error_message = "Encryption at rest with the free S3-managed key."
  }
}

run "only_this_distribution_may_read_the_bucket" {
  # The whole point of Origin Access Control: the bucket is not a website, is not public, and the
  # only reader is this one distribution. A wildcard principal here would quietly make the export
  # world-readable, which is the classic S3 mistake this design exists to avoid.
  assert {
    condition     = !strcontains(aws_s3_bucket_policy.site.policy, "\"Principal\":\"*\"")
    error_message = "The bucket policy must never grant a wildcard principal."
  }

  assert {
    condition     = strcontains(aws_s3_bucket_policy.site.policy, "cloudfront.amazonaws.com")
    error_message = "Only the CloudFront service principal may read the bucket."
  }

  assert {
    condition     = strcontains(aws_s3_bucket_policy.site.policy, aws_cloudfront_distribution.main.arn)
    error_message = "The policy must name THIS distribution, not any distribution in any account."
  }

  assert {
    condition     = !strcontains(aws_s3_bucket_policy.site.policy, "s3:ListBucket")
    error_message = "Reading objects is enough; listing the bucket is not."
  }
}

run "the_bucket_is_reached_through_origin_access_control_not_a_website_endpoint" {
  assert {
    condition     = aws_cloudfront_origin_access_control.site.signing_behavior == "always"
    error_message = "CloudFront must sign every request to S3."
  }

  assert {
    condition     = aws_cloudfront_origin_access_control.site.origin_access_control_origin_type == "s3"
    error_message = "This OAC is for the S3 origin."
  }

  assert {
    condition = anytrue([
      for origin in aws_cloudfront_distribution.main.origin :
      origin.origin_access_control_id == aws_cloudfront_origin_access_control.site.id
    ])
    error_message = "The S3 origin must use the OAC; without it every request is anonymous and denied."
  }
}

# --- the two behaviours ------------------------------------------------------------------------------

run "the_default_behaviour_serves_the_static_export_over_https" {
  assert {
    condition     = aws_cloudfront_distribution.main.default_cache_behavior[0].viewer_protocol_policy == "redirect-to-https"
    error_message = "Plain HTTP must be redirected; the session cookie is Secure and __Host- prefixed."
  }

  assert {
    condition = anytrue([
      for association in aws_cloudfront_distribution.main.default_cache_behavior[0].function_association :
      association.event_type == "viewer-request" &&
      association.function_arn == aws_cloudfront_function.rewrite_index.arn
    ])
    error_message = "A static export has no server to map /stocks to /stocks/index.html; the function does it."
  }
}

run "the_api_behaviour_never_caches_and_forwards_what_a_session_needs" {
  # This is a SECURITY property, not a performance choice. API responses are selected by the session
  # cookie. A cached /api/v1/me would be served to the next visitor, handing them someone else's
  # identity. Caching is therefore disabled outright rather than tuned.
  assert {
    condition = anytrue([
      for behaviour in aws_cloudfront_distribution.main.ordered_cache_behavior :
      behaviour.path_pattern == "/api/*" &&
      behaviour.cache_policy_id == data.aws_cloudfront_cache_policy.caching_disabled.id
    ])
    error_message = "/api/* must use the managed CachingDisabled policy."
  }

  assert {
    condition = anytrue([
      for behaviour in aws_cloudfront_distribution.main.ordered_cache_behavior :
      behaviour.path_pattern == "/api/*" &&
      behaviour.origin_request_policy_id == data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    ])
    error_message = "The API needs the cookie, the query string and Origin forwarded; the Host header must stay the ALB's."
  }

  assert {
    condition = anytrue([
      for behaviour in aws_cloudfront_distribution.main.ordered_cache_behavior :
      behaviour.path_pattern == "/api/*" &&
      alltrue([for verb in ["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"] :
      contains(behaviour.allowed_methods, verb)])
    ])
    error_message = "Sign-in POSTs and follow PUT/DELETEs must reach the backend."
  }
}

run "the_rewrite_function_never_touches_the_api" {
  # Appending /index.html to /api/v1/me would 404 every request. The function belongs to the static
  # behaviour only.
  assert {
    condition = alltrue([
      for behaviour in aws_cloudfront_distribution.main.ordered_cache_behavior :
      behaviour.path_pattern != "/api/*" || length(behaviour.function_association) == 0
    ])
    error_message = "The /api/* behaviour must have no function association."
  }
}

run "the_api_origin_is_the_load_balancer_carrying_the_shared_secret" {
  assert {
    condition = anytrue([
      for origin in aws_cloudfront_distribution.main.origin :
      origin.domain_name == var.alb_origin_domain
    ])
    error_message = "One origin must be the load balancer named by var.alb_origin_domain."
  }

  assert {
    condition = anytrue([
      for origin in aws_cloudfront_distribution.main.origin :
      anytrue([for config in origin.custom_origin_config : config.origin_protocol_policy == "http-only"])
    ])
    error_message = "CloudFront reaches the ALB over plain HTTP (ADR 004); there is no certificate on the ALB."
  }

  # The value is a secret, so the test asserts only that the header is sent under the name the ALB
  # listener rule matches on. Getting the name wrong means a 403 for every API call.
  assert {
    condition = anytrue([
      for origin in aws_cloudfront_distribution.main.origin :
      anytrue([for header in origin.custom_header : header.name == "X-Origin-Verify"])
    ])
    error_message = "Without X-Origin-Verify the ALB's default action returns 403."
  }
}

# --- the decisions that are easy to get wrong ---------------------------------------------------------

run "the_distribution_can_exist_before_the_application_does" {
  # This root is applied FIRST, when there is no ALB at all, and re-applied after every rebuild with
  # the new one. So the origin domain must have a harmless default rather than being required.
  assert {
    condition     = var.alb_origin_domain != ""
    error_message = "alb_origin_domain needs a placeholder default so the edge can be applied on its own."
  }
}

run "the_api_keeps_its_own_error_responses" {
  # custom_error_response is DISTRIBUTION-WIDE: it cannot be limited to one behaviour. Mapping 404
  # to /404.html would therefore replace the backend's own 404 JSON -- which the API returns by
  # design for a foreign resource -- with a page of HTML. A prettier static 404 is not worth
  # corrupting the API contract.
  assert {
    condition     = length(aws_cloudfront_distribution.main.custom_error_response) == 0
    error_message = "No custom_error_response: it would rewrite the API's own 404s into HTML."
  }
}

run "the_cheaper_price_class_is_used" {
  assert {
    condition     = aws_cloudfront_distribution.main.price_class == "PriceClass_200"
    error_message = "PriceClass_200 is the cheapest class that includes India; PriceClass_All costs more for no benefit."
  }
}

# --- what the application stack reads ------------------------------------------------------------------

run "the_shared_secret_is_stored_encrypted_and_write_only" {
  assert {
    condition     = aws_ssm_parameter.origin_verify.type == "SecureString"
    error_message = "The shared origin secret is encrypted at rest."
  }

  # `value` is Optional+Computed, so a mock always invents one and asserting it is null is
  # impossible for correct code (learned in P7c). The guarantee that the plaintext attribute is
  # never used lives in infra/tests/test_infra_hygiene.py instead; here we assert the write-only
  # path is the one in use.
  assert {
    condition     = aws_ssm_parameter.origin_verify.value_wo_version != null
    error_message = "The secret must be written with value_wo, which never reaches the state file."
  }
}

run "the_stack_is_told_the_https_url_to_call_itself" {
  # app_env = production makes the backend refuse to start unless PUBLIC_BASE_URL is https, and
  # switches the cookie to the __Host- prefix. This parameter is where that URL comes from.
  assert {
    condition     = aws_ssm_parameter.public_base_url.value == "https://${aws_cloudfront_distribution.main.domain_name}"
    error_message = "public_base_url must be https:// plus this distribution's domain."
  }

  assert {
    condition     = aws_ssm_parameter.public_base_url.type == "String"
    error_message = "A public URL is not a secret; SecureString would cost a KMS call for nothing."
  }
}

run "the_outputs_name_things_and_never_carry_the_secret" {
  assert {
    condition     = output.public_base_url == "https://${aws_cloudfront_distribution.main.domain_name}"
    error_message = "The output is how the owner learns the domain to register with Google."
  }

  assert {
    condition     = output.site_bucket_name == aws_s3_bucket.site.bucket
    error_message = "The bucket name is needed by `aws s3 sync` and by the P8 pipeline."
  }

  assert {
    condition     = output.distribution_id == aws_cloudfront_distribution.main.id
    error_message = "The distribution id is needed to create an invalidation after a frontend deploy."
  }
}

# --- the guards that stop an expensive mistake -----------------------------------------------------------

run "any_region_other_than_mumbai_is_rejected" {
  command = plan

  variables {
    region = "us-east-1"
  }

  expect_failures = [var.region]
}

run "a_malformed_account_id_is_rejected" {
  command = plan

  variables {
    allowed_account_id = "12345"
  }

  expect_failures = [var.allowed_account_id]
}
