# infra/bootstrap: creates the ONE bucket that stores Terraform's state for the rest of the project.
#
# Why it is separate: Terraform needs somewhere to keep its state BEFORE it can manage anything, and
# that place must survive `terraform destroy` of the application stack. So this small root module is
# applied once, keeps its own state on your machine (there is deliberately no `backend` block here),
# and is only ever destroyed if you abandon the whole project.
#
# This file only says WHICH Terraform and WHICH provider version this folder needs. It contains no
# resources.
terraform {
  # 1.11: first release where S3-native state locking is generally available (used by the stack that
  # will store its state in the bucket created here) and where write-only attributes exist.
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source = "hashicorp/aws"
      # "~> 6.65": 6.65 or newer, never 7.x.
      version = "~> 6.65"
    }
  }
}
