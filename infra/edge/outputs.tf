# infra/edge/outputs.tf
#
# The X-Origin-Verify secret is deliberately not published here. It reaches the application stack
# through SSM, where IAM decides who may read it; an output would put it in `terraform output` and
# in plan output instead.

output "cloudfront_domain_name" {
  description = "The permanent domain. Register it with Google once, and never again."
  value       = aws_cloudfront_distribution.main.domain_name
}

output "public_base_url" {
  description = "What the application calls itself. The stack reads the same value from SSM."
  value       = "https://${aws_cloudfront_distribution.main.domain_name}"
}

output "site_bucket_name" {
  description = "Where the static export goes: aws s3 sync frontend/out s3://<this>/ --delete"
  value       = aws_s3_bucket.site.bucket
}

output "distribution_id" {
  description = "Needed to invalidate the cache after a frontend deploy."
  value       = aws_cloudfront_distribution.main.id
}
