# The credential compatibility test (P7a, step A).
#
# HOW TO READ THIS FILE
#   `terraform test` runs every `run` block below. Each `run` plans the configuration in this folder
#   and then checks the `assert` conditions. `command = plan` means Terraform only READS: it can look
#   things up (a "data source"), but it cannot create, change or delete anything. So this test can
#   never create infrastructure, whatever the configuration contains.
#
#   There is no `mock_provider` here on purpose. This test uses the REAL AWS provider and your REAL
#   credentials, because the whole point is to prove they work together. The provider finds the
#   credentials the normal way: the AWS_PROFILE environment variable (the `aws login` session), or
#   the AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / AWS_SESSION_TOKEN variables (the export-credentials
#   fallback).
#
# WHAT IS EXPECTED TO EXIST (the implementation contract, written before the implementation)
#   data "aws_caller_identity" "current" {}    who am I, according to AWS?
#   data "aws_region" "current" {}             which region am I talking to?
#
# WHY THIS TEST IS RED TODAY
#   main.tf does not exist yet, so those two data sources are not declared. Terraform reports
#   "reference to undeclared resource". That is the right reason to fail: the code is missing.

run "the_identity_is_the_intended_admin_user" {
  command = plan

  assert {
    condition     = endswith(data.aws_caller_identity.current.arn, ":user/srinivas")
    error_message = "Terraform is not running as the IAM user 'srinivas'. Check AWS_PROFILE (stock-analyst-admin) or the exported credentials."
  }
}

run "the_account_id_looks_like_an_account_id" {
  command = plan

  assert {
    condition     = can(regex("^[0-9]{12}$", data.aws_caller_identity.current.account_id))
    error_message = "The account ID is not 12 digits, so the credentials were not resolved properly."
  }
}

run "the_region_is_mumbai" {
  command = plan

  assert {
    condition     = data.aws_region.current.region == "ap-south-1"
    error_message = "The provider is not using ap-south-1 (Mumbai). Everything in this project lives there."
  }
}
