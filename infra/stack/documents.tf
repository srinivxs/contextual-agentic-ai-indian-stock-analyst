# infra/stack/documents.tf
#
# Where the worker keeps the filings it fetches from BSE (go-live; ADR 008's "private documents
# bucket"). On a laptop they sit in a folder; here the worker is told BLOB_BUCKET and writes them to
# this bucket instead (backend/src/app/blobs.py, S3BlobStore). Only the application's own role can
# read or write it (identity.tf), and nothing is ever served from it: users get the official BSE
# link, never our copy.
#
# It belongs to the ephemeral stack and is destroyed with it after every session. That is fine:
# the filings are public documents, and the next session fetches them again. force_destroy lets
# destroy empty it first; without it, `terraform destroy` stops at a bucket that is not empty.
#
# COST: storage is about 0.4 GB of PDFs for a few hours (a fraction of a cent), plus one PUT per
# filing fetched (about 85 a session) and one GET per filing read.

resource "aws_s3_bucket" "documents" {
  # Bucket names are global across all of AWS; the account id makes this one unique.
  bucket        = "${local.name_prefix}-documents-${var.allowed_account_id}"
  force_destroy = true

  tags = {
    Name = "${local.name_prefix}-documents"
  }
}

# Belt and braces: even a mistaken policy or ACL later cannot make an object public.
resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ACLs off: who may read an object is decided by IAM alone, in one place.
resource "aws_s3_bucket_ownership_controls" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# Encrypted at rest with S3's own key. A customer-managed KMS key would add a monthly charge and
# a key policy to explain, for public documents.
resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Refuse any request that is not over TLS. boto3 always uses https; this makes it a rule.
resource "aws_s3_bucket_policy" "documents" {
  bucket = aws_s3_bucket.documents.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "RefusePlainHttp"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource = [
          aws_s3_bucket.documents.arn,
          "${aws_s3_bucket.documents.arn}/*",
        ]
        Condition = {
          Bool = { "aws:SecureTransport" = "false" }
        }
      },
    ]
  })

  # The public-access block must be in place before any policy is attached.
  depends_on = [aws_s3_bucket_public_access_block.documents]
}
