# infra/bootstrap/outputs.tf
#
# What this folder hands to the next one. See them after an apply with `terraform output`, or one at
# a time with `terraform output backend_config_hcl`.
#
# A note on the account number: the bucket name contains it, so these outputs print it. It is not a
# secret (it is in every ARN you will ever see), but it stays out of Git: the file you generate from
# backend_config_hcl is infra/stack/backend.hcl, which .gitignore excludes. Mask it if you paste
# these values into a chat.

output "state_bucket_name" {
  description = "The bucket that holds the application stack's Terraform state."
  value       = aws_s3_bucket.state.bucket
}

output "default_tags" {
  description = "The tags every resource in this project carries. The post-destroy audit searches by Project."
  value       = local.default_tags
}

output "backend_config_hcl" {
  description = "Paste into infra/stack/backend.hcl (git-ignored) so the stack stores its state here."

  # This is the whole reason the bootstrap exists. In P7b, infra/stack declares an empty S3 backend
  # block and is initialised with `terraform init -backend-config=backend.hcl`, which is this text.
  # Generating it here means the stack never has the bucket name (and so the account number) written
  # into a committed file.
  #
  #   key           where inside the bucket this stack's state file lives. A second stack would use
  #                 a different key and share the bucket safely.
  #   encrypt       encrypt the state on the way in, on top of the bucket's own default encryption.
  #   use_lockfile  S3-native locking (Terraform 1.11+): while one run holds the lock, S3 itself
  #                 refuses a second writer. Before Terraform 1.11 this needed a separate NoSQL
  #                 table alongside the bucket; S3 now does it alone, so the project has one fewer
  #                 service to create, pay for and explain.
  value = <<-EOT
    bucket       = "${aws_s3_bucket.state.bucket}"
    key          = "stack/terraform.tfstate"
    region       = "${var.region}"
    encrypt      = true
    use_lockfile = true
  EOT
}
