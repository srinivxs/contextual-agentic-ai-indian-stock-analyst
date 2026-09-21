# infra/preflight/outputs.tf
#
# What the preflight found, safe to read out loud or paste into a chat. Nothing here prints the full
# account number, and the full ARN is not printed either because it contains that number.
#
# See them with `terraform plan` (they appear under "Changes to Outputs").

output "identity_name" {
  description = "The IAM user Terraform is running as (name only)."
  value       = local.identity_name
}

output "account_masked" {
  description = "The AWS account, masked: only the last four digits."
  value       = "********${substr(data.aws_caller_identity.current.account_id, 8, 4)}"
}

output "region" {
  description = "The region the provider is using."
  value       = data.aws_region.current.region
}
