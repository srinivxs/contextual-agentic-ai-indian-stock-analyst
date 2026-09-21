# infra/preflight: a throwaway, READ-ONLY check that Terraform can use our AWS credentials.
#
# It creates nothing. Its only job is to answer, before any infrastructure exists:
#   1. Does the AWS provider accept our short-lived `aws login` session?
#   2. If not, does the documented `aws configure export-credentials` fallback work?
#   3. Is the identity the intended admin user, and is the region Mumbai?
#
# This file only says WHICH Terraform and WHICH provider version this folder needs. It contains no
# resources. (The lock file that `terraform init` writes next to it records the exact provider build
# and is committed, so everyone gets the same one.)
terraform {
  # 1.11 is the first release where S3-native state locking and write-only attributes are generally
  # available (both are used later). Terraform refuses to run on anything older.
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # "~> 6.65" means: 6.65 or newer, but never 7.x. New minor releases are accepted, a new major
      # version (which may break things) is not.
      version = "~> 6.65"
    }
  }
}
