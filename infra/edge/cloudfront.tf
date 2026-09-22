# infra/edge/cloudfront.tf
#
# The distribution: one hostname in front of two completely different things.
#
#   everything else  ->  the private S3 bucket, cached, paths rewritten by a function
#   /api/*           ->  the application load balancer, never cached, session headers forwarded
#
# Serving both from one hostname is not a convenience, it is the security design: the browser sees a
# single origin, so the session cookie is first-party, the backend's Origin check passes, and no CORS
# headers exist anywhere (ADR 012, ADR 013).

# --- the managed policies, looked up by name --------------------------------------------------------
#
# AWS publishes these with fixed UUIDs. Looking them up by name instead means the configuration says
# what it wants rather than quoting an identifier nobody can read, and a wrong name fails the plan
# instead of silently attaching the wrong policy.
data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

# --- the path rewrite --------------------------------------------------------------------------------
#
# A static export has no server to turn /stocks/ into stocks/index.html, and S3 behind Origin Access
# Control is object storage with no directory index. This function does that one job, at the edge,
# in under a millisecond. See functions/rewrite-index.js and its test.
resource "aws_cloudfront_function" "rewrite_index" {
  name    = "${local.name_prefix}-rewrite-index"
  runtime = "cloudfront-js-2.0"
  comment = "Maps static-export routes to their index.html object keys"
  publish = true
  code    = file("${path.module}/functions/rewrite-index.js")
}

resource "aws_cloudfront_distribution" "main" {
  enabled         = true
  comment         = "${local.name_prefix}: static site and API on one origin"
  price_class     = var.price_class
  is_ipv6_enabled = true

  # NO default_root_object ON PURPOSE.
  #
  # It would map "/" to "/index.html", which is exactly what the function already does. Two
  # mechanisms doing the same job means the next person has to work out which one is in force. The
  # function is the single place that turns a request path into an object key.

  # --- where the content comes from ------------------------------------------------------------------

  origin {
    origin_id                = local.s3_origin_id
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  origin {
    origin_id   = local.alb_origin_id
    domain_name = var.alb_origin_domain

    custom_origin_config {
      http_port  = 80
      https_port = 443

      # Plain HTTP to the load balancer. The ALB has no certificate and no custom domain, so there
      # is nothing to present; the hop runs inside AWS and the listener accepts only CloudFront's
      # published address ranges (ADR 004/008). Named as a tradeoff there, not an oversight.
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }

    # The shared secret the ALB listener rule matches on. Without this header the listener's default
    # action returns 403, so reaching the API requires coming through this distribution -- belt and
    # braces behind the security group's CloudFront prefix list.
    custom_header {
      name  = "X-Origin-Verify"
      value = random_password.origin_verify.result
    }
  }

  # --- the static site --------------------------------------------------------------------------------

  default_cache_behavior {
    target_origin_id = local.s3_origin_id

    # The session cookie is Secure and, in production, carries the __Host- prefix, which browsers
    # accept only over HTTPS. Plain HTTP is redirected rather than served.
    viewer_protocol_policy = "redirect-to-https"

    # Static files are read, never written.
    allowed_methods = ["GET", "HEAD"]
    cached_methods  = ["GET", "HEAD"]

    cache_policy_id = data.aws_cloudfront_cache_policy.caching_optimized.id
    compress        = true

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.rewrite_index.arn
    }
  }

  # --- the API ------------------------------------------------------------------------------------------

  ordered_cache_behavior {
    path_pattern     = "/api/*"
    target_origin_id = local.alb_origin_id

    viewer_protocol_policy = "redirect-to-https"

    # Sign-in POSTs, follow PUT and DELETE, and the browser's preflight OPTIONS all have to reach
    # the backend, so every method is allowed here.
    allowed_methods = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods  = ["GET", "HEAD"]

    # CACHING OFF IS A SECURITY PROPERTY, NOT A TUNING CHOICE.
    #
    # Every API response is selected by the session cookie. A cached /api/v1/me would be handed to
    # the next visitor as their own identity. So caching is disabled outright rather than tuned.
    cache_policy_id = data.aws_cloudfront_cache_policy.caching_disabled.id

    # Forwards the cookie, the query string and Origin -- everything the session and the backend's
    # Origin check need -- while leaving the Host header as the load balancer's own name, which is
    # what "ExceptHostHeader" means.
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id

    compress = true

    # NO function_association HERE. Appending /index.html to /api/v1/me would 404 every request.
  }

  # --- the rest ------------------------------------------------------------------------------------------

  # NO custom_error_response ANYWHERE IN THIS RESOURCE.
  #
  # It is distribution-wide: it cannot be limited to one behaviour. Mapping 404 to /404.html would
  # therefore replace the backend's own 404 JSON -- which the API returns by design for a resource
  # belonging to someone else -- with a page of HTML, silently breaking the API contract. The cost is
  # that a missing static path shows S3's bare error. That is the better trade.

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # The free default *.cloudfront.net certificate. A custom domain would need an ACM certificate in
  # us-east-1 and a domain to put in it; neither is justified for a demo (the project notes forbids the
  # custom domain until something proves it necessary).
  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = {
    Name = "${local.name_prefix}-cdn"
  }
}
