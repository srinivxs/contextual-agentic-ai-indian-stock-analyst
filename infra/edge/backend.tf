# infra/edge/backend.tf
#
# Where this root keeps its state: the same bucket infra/bootstrap created, under a different key.
# One bucket, three state files (bootstrap's is local; stack/ and edge/ live here).
#
# The block is EMPTY on purpose. Every setting it needs (which bucket, which key, which region)
# would otherwise be written here, and the bucket's name contains the AWS account number. Instead
# the settings arrive at init time from backend.hcl, which .gitignore excludes:
#
#   terraform init -backend-config=backend.hcl
#
# backend.hcl.example, beside this file, shows what to put in it.
#
# There is no separate lock table. `use_lockfile = true` in backend.hcl turns on S3-native locking,
# where S3 itself refuses a second writer while one run holds the lock (Terraform 1.11+).
terraform {
  backend "s3" {}
}
