# infra/bootstrap/main.tf
#
# Creates the one thing Terraform needs before it can manage anything else: a private, versioned S3
# bucket that will hold the state file for the application stack (infra/stack, written in P7b).
#
# THE CHICKEN AND EGG
#   Terraform records what it created in a "state file". For the application stack that file lives in
#   S3, so the whole team (here: any machine) sees the same truth and two runs cannot fight over it.
#   But something has to create that bucket first, and it cannot store its own state in a bucket that
#   does not exist yet. So this folder is the exception: it keeps its state locally (there is no
#   `backend` block anywhere here), is applied once, and is never destroyed with the rest.
#
# COST
#   Effectively nothing: a state file is a few tens of kilobytes. S3 in Mumbai is $0.025 per GB-month,
#   so this bucket is under one cent a month. It is the only thing that survives `terraform destroy`.

# --- who Terraform is talking to -----------------------------------------------------------------
#
# Two guards, the same ones the preflight proved work:
#   region              written from a variable that only accepts Mumbai, so it always wins over any
#                       AWS_REGION left lying around in a terminal.
#   allowed_account_ids the provider asks AWS which account the credentials belong to and REFUSES to
#                       continue if it is not this one. The cost gate "am I in the right account?",
#                       enforced in code rather than by habit.
#
# `default_tags` puts the same three tags on every resource this provider creates, without repeating
# them. That is what makes the post-destroy audit possible: search by tag, find anything forgotten.
provider "aws" {
  region              = var.region
  allowed_account_ids = [var.allowed_account_id]

  default_tags {
    tags = local.default_tags
  }
}

locals {
  # Every resource in this project carries these. Project is what the "did I leave anything running?"
  # audit searches for.
  default_tags = {
    Project     = "stock-analyst"
    Environment = "demo"
    ManagedBy   = "terraform"
  }

  # S3 bucket names are globally unique across every AWS account in the world, so a plain name like
  # "tfstate" is long gone. Account id plus region makes it unique and self-describing. The account
  # id is not a secret, but it is not written down in Git either: it arrives from the environment as
  # TF_VAR_allowed_account_id.
  state_bucket_name = "stock-analyst-tfstate-${var.allowed_account_id}-${var.region}"
}

# --- the bucket ------------------------------------------------------------------------------------
#
# Note how small this is. In S3, the bucket itself is almost nothing; every property below (privacy,
# versioning, encryption, expiry, policy) is a SEPARATE resource that points back at it. That is the
# modern provider's design: one concern per resource, so a change to one never rewrites the others.
resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket_name

  # false = S3 refuses to delete the bucket while anything is still inside it. Our state file is
  # inside it, so "delete it anyway" is never what we want. Written out even though false is the
  # default, because a reader should not have to know the default to know we thought about it.
  force_destroy = false

  lifecycle {
    # A second, stronger guard, enforced by Terraform rather than AWS: `terraform destroy` in this
    # folder fails outright. To remove this bucket on purpose you must first delete these two lines,
    # which is exactly the kind of deliberate act it should take to throw away the state.
    prevent_destroy = true
  }
}

# --- the bucket is private, in four ways plus one -------------------------------------------------
#
# S3 has two separate ways to grant access: ACLs (the old per-object way) and policies (the modern
# way). We switch ACLs off entirely and block every route to "public", so the only way in is an
# explicit IAM permission.
resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  # The four switches AWS gives you. Roughly: refuse public ACLs, ignore any that already exist,
  # refuse a public bucket policy, and ignore any that already exists. All four on is the only
  # setting that leaves no gap.
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    # BucketOwnerEnforced turns ACLs off completely: every object belongs to us and only bucket
    # policies and IAM decide who may read it. One access-control system instead of two.
    object_ownership = "BucketOwnerEnforced"
  }
}

# --- history and encryption -------------------------------------------------------------------------

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    # The undo button. A corrupted or truncated state file is one of the few genuinely painful
    # Terraform failures; with versioning on, the previous state is still an object in the bucket.
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      # AES256 = SSE-S3, encryption with a key S3 manages for us. It is free. KMS would add per-key
      # and per-request charges and a second thing to explain, for no gain on a personal project.
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"

    # An empty filter means "every object in the bucket". The provider requires the block to be
    # present so that the scope of a rule is always stated, never assumed.
    filter {}

    # Versioning keeps every old copy forever unless told otherwise, and each one costs storage.
    # Thirty days is far longer than we would ever need to roll back, and then they go.
    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  # A lifecycle rule about "noncurrent versions" only makes sense once versioning exists. Terraform
  # cannot infer that from the arguments, so we say it.
  depends_on = [aws_s3_bucket_versioning.state]
}

# --- only HTTPS may talk to this bucket ---------------------------------------------------------
#
# A bucket policy is a permissions document attached to the bucket. This one grants nothing: it is a
# single Deny. Deny always beats Allow in IAM, so whatever permissions we hold elsewhere, a request
# that arrives over plain HTTP is refused.
#
# Written with jsonencode rather than a policy-document data source because the JSON is short, and
# this way what you read here is exactly what AWS receives.
resource "aws_s3_bucket_policy" "state_tls_only" {
  bucket = aws_s3_bucket.state.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyUnencryptedTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"

        # Both lines are needed: the bucket itself (operations like ListBucket) and everything in it
        # (operations like GetObject). Naming only one leaves the other reachable over HTTP.
        Resource = [
          aws_s3_bucket.state.arn,
          "${aws_s3_bucket.state.arn}/*",
        ]

        # aws:SecureTransport is a condition key AWS sets on every request: true for HTTPS, false for
        # HTTP. So this reads "deny everyone everything when the connection is not encrypted".
        Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      },
    ]
  })

  # Apply the privacy settings first. A policy that arrives while the public-access block is still
  # being created can be rejected, and the order costs us nothing.
  depends_on = [aws_s3_bucket_public_access_block.state]
}
