# infra/edge/frontend.tf
#
# The bucket holding the Next.js static export, and the one door into it.
#
# THE POINT OF THIS FILE: the bucket is private. It is not an S3 website, it has no public policy,
# and nothing on the internet can read an object from it directly. The only reader is one CloudFront
# distribution, which proves who it is by signing each request (Origin Access Control). That is the
# difference between "a static site on S3" as it is usually done and as it should be done.

resource "aws_s3_bucket" "site" {
  bucket = local.site_bucket_name

  # Unlike the state bucket, everything in here is rebuildable in seconds by running `npm run build`
  # and syncing again. So it may be emptied and destroyed without ceremony.
  force_destroy = true

  tags = {
    Name = "${local.name_prefix}-site"
  }
}

# Four separate switches, all on. Any one of them left off is the classic way a private bucket
# quietly becomes a public one.
resource "aws_s3_bucket_public_access_block" "site" {
  bucket = aws_s3_bucket.site.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ACLs are a pre-IAM mechanism nothing here needs. BucketOwnerEnforced disables them outright, so
# access is decided only by the bucket policy below.
resource "aws_s3_bucket_ownership_controls" "site" {
  bucket = aws_s3_bucket.site.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Encryption at rest with the S3-managed key, which costs nothing.
resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# --- how CloudFront proves who it is ---------------------------------------------------------------
#
# Origin Access Control: CloudFront signs every request to S3 with SigV4, as a caller S3 recognises.
# It replaces the older Origin Access Identity, which used a special user rather than a signature.
resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "${local.name_prefix}-site"
  description                       = "Lets only this distribution read the static site bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# --- the only grant on the bucket -------------------------------------------------------------------
#
# Read one object, as the CloudFront service, and only when the request comes from THIS distribution.
# Without the SourceArn condition, any CloudFront distribution in any AWS account could be pointed at
# this bucket and would be allowed to read it.
#
# Written with jsonencode rather than aws_iam_policy_document for the same reason as the bootstrap's
# policy: what is written here is exactly what is sent, with no rendering surprises in between.
#
# s3:GetObject only. Not ListBucket: CloudFront asks for a key by name and never needs an index, and
# granting a listing would let anyone who found the door enumerate everything behind it.
resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowThisDistributionToReadObjects"
        Effect = "Allow"
        Principal = {
          Service = "cloudfront.amazonaws.com"
        }
        Action   = "s3:GetObject"
        Resource = "${aws_s3_bucket.site.arn}/*"
        Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.main.arn
          }
        }
      },
    ]
  })

  # The public access block must exist first: applying a bucket policy to a bucket that is not yet
  # locked down leaves a window, however short, in which the policy is evaluated without it.
  depends_on = [aws_s3_bucket_public_access_block.site]
}
