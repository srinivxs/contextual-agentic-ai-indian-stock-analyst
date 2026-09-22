# infra/cicd/backend.tf
#
# Where this root keeps its state: the same bucket infra/bootstrap created, under its own key.
# One bucket, three remote state files (stack/, edge/ and cicd/; the bootstrap's is local).
#
# The block is EMPTY on purpose, exactly as in the other roots: the bucket's name contains the AWS
# account number, so the settings arrive at init time from the git-ignored backend.hcl:
#
#   terraform init -backend-config=backend.hcl
#
# backend.hcl.example, beside this file, shows what to put in it.
terraform {
  backend "s3" {}
}
