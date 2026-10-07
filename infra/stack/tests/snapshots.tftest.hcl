# Offline tests for keeping the database between sessions (the owner, 2026-10-08).
#
# demo-down.ps1 used to destroy the database with everything in it, so every demo-up began empty and
# the worker spent 30 to 45 minutes (and about $0.56 of Bedrock) filling it again. Now the destroy
# saves the database as a snapshot named by demo-up, and the next demo-up restores the newest one.
#
#   db_snapshot_identifier         the snapshot to restore from; "" = a new, empty database
#   db_final_snapshot_identifier   the name to save under on destroy; "" = save nothing (as before)
#
# Both default to "", so a plain `terraform apply` behaves exactly as it always did.
#
# WHAT THESE TESTS CANNOT PROVE
#   That RDS restores the snapshot, or that the provider sets the new master password after the
#   restore (it does in v6.66: internal/service/rds/instance.go modifies MasterUserPassword from
#   password_wo after RestoreDBInstanceFromDBSnapshot). The first real session proves both.

mock_provider "aws" {
  mock_data "aws_ssm_parameter" {
    defaults = {
      value = "https://mock-distribution.cloudfront.net"
    }
  }

  mock_data "aws_availability_zones" {
    defaults = {
      names = ["ap-south-1a", "ap-south-1b", "ap-south-1c"]
    }
  }

  mock_data "aws_ec2_managed_prefix_list" {
    defaults = {
      id = "pl-0mockcloudfront"
    }
  }

  mock_data "aws_kms_alias" {
    defaults = {
      target_key_arn = "arn:aws:kms:ap-south-1:123456789012:key/mock-ssm-key"
    }
  }

  mock_resource "aws_db_instance" {
    defaults = {
      address  = "mock-db.ap-south-1.rds.amazonaws.com"
      endpoint = "mock-db.ap-south-1.rds.amazonaws.com:5432"
    }
  }
}

override_data {
  target = data.aws_ssm_parameter.ecr_repository_url
  values = {
    value = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/stock-analyst-demo-backend"
  }
}

override_data {
  target = data.aws_ecr_image.backend
  values = {
    image_digest = "sha256:1111111111111111111111111111111111111111111111111111111111111111"
  }
}

# Distinct, valid ARNs per resource (see compute.tftest.hcl for why override, not mock).
override_resource {
  target = aws_iam_role.api_execution
  values = {
    arn = "arn:aws:iam::123456789012:role/mock-api-execution"
  }
}

override_resource {
  target = aws_iam_role.migrate_execution
  values = {
    arn = "arn:aws:iam::123456789012:role/mock-migrate-execution"
  }
}

override_resource {
  target = aws_iam_role.task
  values = {
    arn = "arn:aws:iam::123456789012:role/mock-task"
  }
}

override_resource {
  target = aws_lb.main
  values = {
    arn      = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:loadbalancer/app/mock-alb/0123456789abcdef"
    dns_name = "mock-alb-1234567890.ap-south-1.elb.amazonaws.com"
  }
}

override_resource {
  target = aws_lb_target_group.api
  values = {
    arn = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:targetgroup/mock-api/0123456789abcdef"
  }
}

override_resource {
  target = aws_lb_listener.http
  values = {
    arn = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:listener/app/mock-alb/0123456789abcdef/0123456789abcdef"
  }
}

override_resource {
  target = aws_ecs_cluster.main
  values = {
    arn = "arn:aws:ecs:ap-south-1:123456789012:cluster/stock-analyst-demo"
  }
}

override_resource {
  target = aws_s3_bucket.documents
  values = {
    id  = "stock-analyst-demo-documents-123456789012"
    arn = "arn:aws:s3:::stock-analyst-demo-documents-123456789012"
  }
}

variables {
  allowed_account_id   = "123456789012"
  google_client_id     = "mock-client-id.apps.googleusercontent.com"
  google_client_secret = "mock-google-client-secret"
  allowed_emails       = "owner@example.com,friend@example.org"
}

# --- without names: exactly as before ------------------------------------------------------------

run "without_names_the_database_starts_empty_and_nothing_is_saved" {
  # The mock invents a value for the computed snapshot_identifier, so the name we pass is checked.
  assert {
    condition     = local.db_restore_from == null
    error_message = "No snapshot named: a new, empty database."
  }

  assert {
    condition     = aws_db_instance.main.skip_final_snapshot == true
    error_message = "No name to save under: the destroy saves nothing, as before."
  }

  assert {
    condition     = aws_db_instance.main.final_snapshot_identifier == null
    error_message = "No final snapshot name without one passed in."
  }
}

# --- demo-up's names: restore one, save the next --------------------------------------------------

run "demo_up_restores_a_snapshot_and_names_the_one_saved_on_destroy" {
  state_key = "restored" # a database of its own, created from the snapshot

  variables {
    db_snapshot_identifier       = "stock-analyst-demo-db-20261008-0930"
    db_final_snapshot_identifier = "stock-analyst-demo-db-20261009-1015"
  }

  assert {
    condition     = aws_db_instance.main.snapshot_identifier == "stock-analyst-demo-db-20261008-0930"
    error_message = "The database is restored from the snapshot demo-up found."
  }

  assert {
    condition     = aws_db_instance.main.skip_final_snapshot == false
    error_message = "With a name, the destroy saves the database first."
  }

  assert {
    condition     = aws_db_instance.main.final_snapshot_identifier == "stock-analyst-demo-db-20261009-1015"
    error_message = "The destroy saves under the name demo-up chose."
  }

  # The rest of the teardown story is unchanged: nothing else may block or bill.
  assert {
    condition     = aws_db_instance.main.deletion_protection == false && aws_db_instance.main.backup_retention_period == 0
    error_message = "Deletion protection stays off and automated backups stay off."
  }
}

run "a_newer_snapshot_never_replaces_the_running_database" {
  state_key = "restored" # the same, running database as the run above

  # demo-up run again in the same session, after a newer save: the live database must be kept.
  variables {
    db_snapshot_identifier       = "stock-analyst-demo-db-20261010-0800"
    db_final_snapshot_identifier = "stock-analyst-demo-db-20261010-0815"
  }

  assert {
    condition     = aws_db_instance.main.snapshot_identifier == "stock-analyst-demo-db-20261008-0930"
    error_message = "A different snapshot must not replace (and so wipe) a running database."
  }

  assert {
    condition     = aws_db_instance.main.final_snapshot_identifier == "stock-analyst-demo-db-20261010-0815"
    error_message = "The name to save under follows the latest demo-up."
  }
}

# --- only our own snapshot names ------------------------------------------------------------------

run "a_snapshot_to_restore_must_be_one_of_ours" {
  command = plan

  variables {
    db_snapshot_identifier = "someone-elses-production-db"
  }

  expect_failures = [var.db_snapshot_identifier]
}

run "a_name_to_save_under_must_be_one_of_ours" {
  command = plan

  variables {
    db_final_snapshot_identifier = "Stock_Analyst"
  }

  expect_failures = [var.db_final_snapshot_identifier]
}
