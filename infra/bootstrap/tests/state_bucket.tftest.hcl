# Offline tests for the Terraform state bucket (P7a, step B).
#
# HOW TO READ THIS FILE
#   `mock_provider "aws"` swaps the real AWS provider for a fake one that has the same schema but
#   talks to nothing: no credentials, no network, no cost, nothing created. Terraform "applies" the
#   configuration against the fake, then each `assert` inspects the result. This is how we prove the
#   bucket is configured the way we designed it BEFORE a real bucket exists.
#
#   A mock invents values for attributes AWS would normally choose (ids, ARNs). We fix the one we
#   rely on, the bucket ARN, so the policy checks below have something exact to compare with.
#
# WHAT THE IMPLEMENTATION MUST CALL THINGS (the contract, written before the implementation)
#   variables   region, allowed_account_id
#   resources   aws_s3_bucket.state
#               aws_s3_bucket_public_access_block.state
#               aws_s3_bucket_ownership_controls.state
#               aws_s3_bucket_versioning.state
#               aws_s3_bucket_server_side_encryption_configuration.state
#               aws_s3_bucket_lifecycle_configuration.state
#               aws_s3_bucket_policy.state_tls_only
#   outputs     state_bucket_name, default_tags, backend_config_hcl
#
# WHY THESE TESTS ARE RED TODAY
#   main.tf, variables.tf and outputs.tf do not exist yet, so none of the names above are declared.
#   Terraform reports "reference to undeclared ..." for each. That is the right reason to fail.
#
# NOT TESTED HERE (Terraform tests cannot see these), so tests/test_infra_hygiene.py checks them:
#   - "Terraform >= 1.11"            (it is the `terraform` block, not a resource)
#   - "cannot be destroyed by mistake" via `lifecycle { prevent_destroy = true }`

mock_provider "aws" {
  mock_resource "aws_s3_bucket" {
    defaults = {
      arn = "arn:aws:s3:::stock-analyst-tfstate-123456789012-ap-south-1"
    }
  }
}

variables {
  allowed_account_id = "123456789012"
}

# --- naming, region and account ---------------------------------------------------------------

run "the_bucket_is_named_for_this_project_account_and_region" {
  assert {
    condition     = aws_s3_bucket.state.bucket == "stock-analyst-tfstate-123456789012-ap-south-1"
    error_message = "The bucket must be called stock-analyst-tfstate-<account id>-<region>."
  }
}

run "any_region_other_than_mumbai_is_rejected" {
  command = plan

  variables {
    region = "us-east-1"
  }

  expect_failures = [var.region]
}

run "a_malformed_account_id_is_rejected" {
  command = plan

  variables {
    allowed_account_id = "not-an-account-id"
  }

  expect_failures = [var.allowed_account_id]
}

# --- the bucket is private ---------------------------------------------------------------------

run "public_access_is_blocked_in_all_four_ways" {
  assert {
    condition     = aws_s3_bucket_public_access_block.state.block_public_acls == true
    error_message = "block_public_acls must be true."
  }

  assert {
    condition     = aws_s3_bucket_public_access_block.state.block_public_policy == true
    error_message = "block_public_policy must be true."
  }

  assert {
    condition     = aws_s3_bucket_public_access_block.state.ignore_public_acls == true
    error_message = "ignore_public_acls must be true."
  }

  assert {
    condition     = aws_s3_bucket_public_access_block.state.restrict_public_buckets == true
    error_message = "restrict_public_buckets must be true."
  }
}

run "acls_are_disabled_so_only_policies_grant_access" {
  assert {
    condition     = aws_s3_bucket_ownership_controls.state.rule[0].object_ownership == "BucketOwnerEnforced"
    error_message = "object_ownership must be BucketOwnerEnforced (this switches ACLs off entirely)."
  }
}

# --- history and encryption --------------------------------------------------------------------

run "every_version_of_the_state_is_kept" {
  assert {
    condition     = aws_s3_bucket_versioning.state.versioning_configuration[0].status == "Enabled"
    error_message = "Versioning must be Enabled: it is the undo button for a damaged state file."
  }
}

run "the_state_is_encrypted_at_rest" {
  assert {
    condition = anytrue([
      for rule in aws_s3_bucket_server_side_encryption_configuration.state.rule :
      anytrue([for d in rule.apply_server_side_encryption_by_default : d.sse_algorithm == "AES256"])
    ])
    error_message = "Server-side encryption must be on (AES256, the free S3-managed key)."
  }
}

run "old_versions_of_the_state_expire_after_30_days" {
  assert {
    condition = anytrue([
      for rule in aws_s3_bucket_lifecycle_configuration.state.rule :
      rule.status == "Enabled" && anytrue([for n in rule.noncurrent_version_expiration : n.noncurrent_days == 30])
    ])
    error_message = "An enabled lifecycle rule must expire noncurrent versions after 30 days."
  }
}

# --- only HTTPS may talk to the bucket ---------------------------------------------------------

run "the_bucket_policy_refuses_any_request_that_is_not_https" {
  assert {
    condition = anytrue([
      for s in jsondecode(aws_s3_bucket_policy.state_tls_only.policy).Statement :
      try(
        s.Effect == "Deny" && s.Principal == "*" && s.Action == "s3:*" &&
        tostring(s.Condition.Bool["aws:SecureTransport"]) == "false",
        false
      )
    ])
    error_message = "The policy needs a Deny statement for everyone (*), action s3:*, when aws:SecureTransport is false."
  }

  assert {
    condition = anytrue([
      for s in jsondecode(aws_s3_bucket_policy.state_tls_only.policy).Statement :
      try(
        contains(s.Resource, aws_s3_bucket.state.arn) && contains(s.Resource, "${aws_s3_bucket.state.arn}/*"),
        false
      )
    ])
    error_message = "The deny must cover both the bucket and every object in it (the ARN and the ARN/*)."
  }
}

# --- protection against accidents --------------------------------------------------------------

run "a_bucket_that_holds_state_is_never_emptied_on_destroy" {
  assert {
    condition     = aws_s3_bucket.state.force_destroy == false
    error_message = "Set force_destroy = false explicitly: this bucket must refuse to be deleted while it still holds state."
  }
}

# --- tags and outputs ---------------------------------------------------------------------------

run "every_resource_will_carry_the_project_tags" {
  assert {
    condition     = output.default_tags["Project"] == "stock-analyst"
    error_message = "Tag Project must be stock-analyst (the post-destroy audit searches by it)."
  }

  assert {
    condition     = output.default_tags["Environment"] == "demo"
    error_message = "Tag Environment must be demo."
  }

  assert {
    condition     = output.default_tags["ManagedBy"] == "terraform"
    error_message = "Tag ManagedBy must be terraform."
  }
}

run "the_bucket_name_is_published_as_an_output" {
  assert {
    condition     = output.state_bucket_name == aws_s3_bucket.state.bucket
    error_message = "Output state_bucket_name must equal the bucket's name."
  }
}

run "the_backend_snippet_uses_native_s3_locking_and_encryption" {
  assert {
    condition     = can(regex("bucket\\s*=\\s*\"stock-analyst-tfstate-123456789012-ap-south-1\"", output.backend_config_hcl))
    error_message = "backend_config_hcl must name the state bucket."
  }

  assert {
    condition     = can(regex("region\\s*=\\s*\"ap-south-1\"", output.backend_config_hcl))
    error_message = "backend_config_hcl must name the region ap-south-1."
  }

  assert {
    condition     = can(regex("use_lockfile\\s*=\\s*true", output.backend_config_hcl))
    error_message = "backend_config_hcl must turn on S3-native locking (use_lockfile = true)."
  }

  assert {
    condition     = can(regex("encrypt\\s*=\\s*true", output.backend_config_hcl))
    error_message = "backend_config_hcl must set encrypt = true."
  }

  assert {
    condition     = !strcontains(lower(output.backend_config_hcl), "dynamodb")
    error_message = "State locking must not use DynamoDB (the S3 lock file replaces it, and DynamoDB is off-limits)."
  }
}
