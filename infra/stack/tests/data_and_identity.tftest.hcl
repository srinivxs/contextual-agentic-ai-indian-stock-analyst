# Offline tests for P7c: the database, the two secrets and the IAM roles.
# (The image registry moved to infra/cicd in P8a; this root only reads its URL.)
#
# HOW TO READ THIS FILE
#   Same shape as tests/network.tftest.hcl. `mock_provider "aws"` swaps in a fake AWS with the same
#   schema that talks to nothing: no credentials, no network, no cost, nothing created. The whole
#   root module is planned against the fake, so the P7b network mocks are repeated here.
#
#   The `random` provider is deliberately NOT mocked. It generates values locally with no network,
#   so letting the real one run is both simpler and a genuine check that the ephemeral block works.
#
# HOW TO RUN IT
#   terraform init -backend=false     providers only: no state, no S3, no credentials
#   terraform test
#
# WHAT THE IMPLEMENTATION MUST CALL THINGS (the contract, written before the implementation)
#   variables   db_password_version
#   ephemeral   random_password.db_master      random_password.db_runtime
#   data        aws_kms_alias.ssm
#   resources   aws_db_subnet_group.main
#               aws_db_parameter_group.main
#               aws_db_instance.main
#               aws_ssm_parameter.database_url            (runtime, read by the api)
#               aws_ssm_parameter.migration_database_url  (admin, never read by the api)
#               (the image registry moved to infra/cicd in P8a; this root only reads its URL)
#               aws_iam_role.api_execution / .migrate_execution / .task
#               aws_iam_role_policy_attachment.api_execution_managed
#               aws_iam_role_policy_attachment.migrate_execution_managed
#               aws_iam_role_policy.api_execution_secrets
#               aws_iam_role_policy.migrate_execution_secrets
#   outputs     db_instance_endpoint, ecr_repository_url, ssm_runtime_parameter_name,
#               ssm_migration_parameter_name, api_execution_role_arn,
#               migrate_execution_role_arn, task_role_arn
#
#   Parameter names are fixed, because the IAM policies must name them exactly:
#       /stock-analyst/demo/database_url             runtime
#       /stock-analyst/demo/migration_database_url   admin
#
#   The policies build parameter ARNs from the region and account variables rather than from the
#   parameters' own arn attributes. That is deliberate: a mock gives every resource of a type the
#   same invented ARN, so reading them back would make the "the api cannot see the admin parameter"
#   assertion pass for the wrong reason.
#
# WHY THESE TESTS ARE RED TODAY
#   None of the names above are declared yet, so Terraform reports "reference to undeclared ...".
#   That is the right reason to fail.
#
# WHAT THESE TESTS CANNOT PROVE
#   That AWS accepts the configuration (P7b was a lesson in that: a mock happily accepted a rule
#   description the real API rejected). That the password truly never reaches the state file can
#   only be shown for certain by inspecting the real state after a real apply, which is a step in
#   the drill. Absence of resources is checked by infra/tests/test_infra_hygiene.py, not here.

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

  # P7d resources, mocked only so the plan completes. Note `id` is deliberately NOT set on
  # aws_iam_role: the run blocks below compare policy attachments by role id, and a shared id would
  # make those comparisons pass no matter which role the policy was attached to.
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/mock-role"
    }
  }

  mock_resource "aws_lb" {
    defaults = {
      arn      = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:loadbalancer/app/mock-alb/0123456789abcdef"
      dns_name = "mock-alb-1234567890.ap-south-1.elb.amazonaws.com"
    }
  }

  mock_resource "aws_lb_target_group" {
    defaults = {
      arn = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:targetgroup/mock-api/0123456789abcdef"
    }
  }

  mock_resource "aws_lb_listener" {
    defaults = {
      arn = "arn:aws:elasticloadbalancing:ap-south-1:123456789012:listener/app/mock-alb/0123456789abcdef/0123456789abcdef"
    }
  }

  mock_resource "aws_ecs_cluster" {
    defaults = {
      arn = "arn:aws:ecs:ap-south-1:123456789012:cluster/stock-analyst-demo"
    }
  }
}

# The registry URL infra/cicd publishes. mock_data above gives EVERY ssm parameter the same value,
# so this one is overridden by name, or the image would be built from the public URL.
override_data {
  target = data.aws_ssm_parameter.ecr_repository_url
  values = {
    value = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/stock-analyst-demo-backend"
  }
}

variables {
  allowed_account_id   = "123456789012"
  google_client_id     = "mock-client-id.apps.googleusercontent.com"
  google_client_secret = "mock-google-client-secret"
  allowed_emails       = "owner@example.com,friend@example.org"
}

# --- the database -----------------------------------------------------------------------------

run "the_database_is_postgresql_sixteen_on_the_smallest_instance" {
  assert {
    condition     = aws_db_instance.main.engine == "postgres"
    error_message = "The engine must be postgres."
  }

  # The major version is pinned and the minor is not: AWS picks the current minor for ap-south-1,
  # which avoids naming a minor that does not exist in this region. A change of MAJOR version is a
  # different database and must never happen silently.
  assert {
    condition     = aws_db_instance.main.engine_version == "16"
    error_message = "The engine version must be exactly \"16\": major pinned, minor chosen by AWS."
  }

  assert {
    condition     = aws_db_instance.main.instance_class == "db.t4g.micro"
    error_message = "The instance class must be db.t4g.micro (the cheapest that runs PostgreSQL)."
  }
}

run "the_database_has_twenty_gigabytes_of_encrypted_gp3_storage" {
  assert {
    condition     = aws_db_instance.main.allocated_storage == 20
    error_message = "Storage must be 20 GB."
  }

  assert {
    condition     = aws_db_instance.main.storage_type == "gp3"
    error_message = "Storage must be gp3."
  }

  # Free, using the AWS-managed key. There is no reason not to.
  assert {
    condition     = aws_db_instance.main.storage_encrypted == true
    error_message = "Storage must be encrypted at rest."
  }
}

run "the_database_is_private_single_az_and_genuinely_disposable" {
  assert {
    condition     = aws_db_instance.main.multi_az == false
    error_message = "Multi-AZ doubles the cost and is not wanted here."
  }

  assert {
    condition     = aws_db_instance.main.publicly_accessible == false
    error_message = "The database must never get a public address."
  }

  # The next three exist so that `terraform destroy` actually works and leaves nothing billing.
  # All three would be wrong in production, which is the point worth being able to explain.
  assert {
    condition     = aws_db_instance.main.deletion_protection == false
    error_message = "Deletion protection must be off, or the end-of-session destroy fails."
  }

  # Without demo-up's name nothing is saved on destroy; with it, the database is kept between
  # sessions on purpose (snapshots.tftest.hcl, ADR 008 amendment).
  assert {
    condition     = aws_db_instance.main.skip_final_snapshot == true
    error_message = "A plain apply (no name to save under) must save nothing on destroy."
  }

  assert {
    condition     = aws_db_instance.main.backup_retention_period == 0
    error_message = "Automated backups are not wanted in a disposable demo environment."
  }
}

run "the_database_sits_only_in_the_isolated_subnets" {
  assert {
    condition     = length(aws_db_subnet_group.main.subnet_ids) == 3
    error_message = "The subnet group must contain exactly one isolated subnet per zone (three in Mumbai)."
  }

  assert {
    condition = alltrue([
      for id in aws_subnet.isolated[*].id :
      contains(tolist(aws_db_subnet_group.main.subnet_ids), id)
    ])
    error_message = "The subnet group must contain every isolated subnet."
  }

  # The decisive half: no subnet with a route to the internet gateway may appear here.
  assert {
    condition = alltrue([
      for id in aws_subnet.public[*].id :
      !contains(tolist(aws_db_subnet_group.main.subnet_ids), id)
    ])
    error_message = "No public subnet may appear in the database's subnet group."
  }

  assert {
    condition     = aws_db_instance.main.db_subnet_group_name == aws_db_subnet_group.main.name
    error_message = "The instance must use this subnet group."
  }
}

run "the_database_is_reachable_only_through_its_own_security_group" {
  assert {
    condition     = length(aws_db_instance.main.vpc_security_group_ids) == 1
    error_message = "Exactly one security group, the one built in P7b for the database."
  }

  assert {
    condition     = contains(aws_db_instance.main.vpc_security_group_ids, aws_security_group.rds.id)
    error_message = "The instance must use the rds security group from P7b."
  }
}

run "connections_must_use_tls" {
  assert {
    condition     = startswith(aws_db_parameter_group.main.family, "postgres16")
    error_message = "The parameter group family must match PostgreSQL 16."
  }

  assert {
    condition = anytrue([
      for p in aws_db_parameter_group.main.parameter :
      p.name == "rds.force_ssl" && tostring(p.value) == "1"
    ])
    error_message = "rds.force_ssl must be 1, so PostgreSQL refuses an unencrypted connection."
  }

  # One setting, deliberately. A parameter group is a place where unrelated tuning accumulates.
  assert {
    condition     = length(aws_db_parameter_group.main.parameter) == 1
    error_message = "The parameter group must contain only rds.force_ssl."
  }

  assert {
    condition     = aws_db_instance.main.parameter_group_name == aws_db_parameter_group.main.name
    error_message = "The instance must use this parameter group, or force_ssl does nothing."
  }
}

# --- the secrets --------------------------------------------------------------------------------

run "the_password_is_never_written_to_the_state_file" {
  # `password` is the ordinary attribute, and the provider documents that it is stored in state in
  # plain text. Leaving it null proves we used password_wo instead, which is sent and discarded.
  assert {
    condition     = aws_db_instance.main.password == null
    error_message = "password must stay null; the write-only password_wo carries the value."
  }

  # password_wo has no value to compare against, so the provider only re-sends it when this number
  # changes. It is an ordinary attribute, so it is readable here.
  assert {
    condition     = aws_db_instance.main.password_wo_version == var.db_password_version
    error_message = "password_wo_version must follow the db_password_version variable."
  }

  # Secrets Manager is not used: it costs $0.40 per secret per month and stores JSON that ECS
  # cannot turn back into a connection URL.
  assert {
    condition     = aws_db_instance.main.manage_master_user_password == null
    error_message = "The RDS-managed master secret (Secrets Manager) must not be used."
  }
}

run "the_two_connection_urls_are_stored_separately_and_encrypted" {
  assert {
    condition     = aws_ssm_parameter.database_url.name == "/stock-analyst/demo/database_url"
    error_message = "The runtime parameter has a fixed name the IAM policy depends on."
  }

  assert {
    condition     = aws_ssm_parameter.migration_database_url.name == "/stock-analyst/demo/migration_database_url"
    error_message = "The admin parameter has a fixed name the IAM policy depends on."
  }

  assert {
    condition = (
      aws_ssm_parameter.database_url.type == "SecureString" &&
      aws_ssm_parameter.migration_database_url.type == "SecureString"
    )
    error_message = "Both parameters must be SecureString, encrypted with the AWS-managed key."
  }

  # THERE IS NO ASSERTION HERE THAT `value` IS NULL, AND THAT IS DELIBERATE.
  #
  # The first version of this file asserted it, by analogy with the database password above, and it
  # failed: the mock reported `(sensitive value)`. The reason is a schema difference, not a mistake
  # in the configuration. `aws_ssm_parameter.value` is Optional AND COMPUTED, because AWS can read a
  # parameter back, so a mock provider invents a value for it when the configuration does not set
  # one. `aws_db_instance.password` is not computed, so the mock leaves it null and the assertion
  # above is meaningful.
  #
  # An assertion that no correct implementation can pass is a broken test, so it was removed rather
  # than softened. The guarantee is covered twice elsewhere instead:
  #   - infra/tests/test_infra_hygiene.py fails if `value =` ever appears in secrets.tf at all;
  #   - the apply drill downloads the real state file from S3 and searches it for the credentials,
  #     which is the only place the question can actually be settled.
  assert {
    condition = (
      aws_ssm_parameter.database_url.value_wo_version == var.db_password_version &&
      aws_ssm_parameter.migration_database_url.value_wo_version == var.db_password_version
    )
    error_message = "Both parameters must re-send when db_password_version changes."
  }
}

# --- the identity boundary ------------------------------------------------------------------------

run "the_api_may_read_only_the_runtime_parameter" {
  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.api_execution_secrets.policy).Statement :
      contains(s.Resource, "arn:aws:ssm:ap-south-1:123456789012:parameter/stock-analyst/demo/database_url")
    ])
    error_message = "The api execution role must be able to read the runtime parameter."
  }

  # The security boundary of this milestone. ADR 011 says the runtime database role has no DDL
  # rights; that guarantee is worthless if the api container can simply read the admin URL.
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.api_execution_secrets.policy).Statement :
      !contains(s.Resource, "arn:aws:ssm:ap-south-1:123456789012:parameter/stock-analyst/demo/migration_database_url")
    ])
    error_message = "The api execution role must NOT be able to read the admin parameter."
  }

  assert {
    condition     = aws_iam_role_policy.api_execution_secrets.role == aws_iam_role.api_execution.id
    error_message = "This policy belongs on the api execution role and nowhere else."
  }
}

run "the_migration_role_may_read_both_parameters" {
  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.migrate_execution_secrets.policy).Statement :
      contains(s.Resource, "arn:aws:ssm:ap-south-1:123456789012:parameter/stock-analyst/demo/migration_database_url")
    ])
    error_message = "The migration role must be able to read the admin parameter."
  }

  # It also needs the runtime URL, because the migration is what creates the no-DDL runtime role
  # inside PostgreSQL and must know the password it is setting.
  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.migrate_execution_secrets.policy).Statement :
      contains(s.Resource, "arn:aws:ssm:ap-south-1:123456789012:parameter/stock-analyst/demo/database_url")
    ])
    error_message = "The migration role must also read the runtime URL, to create that role in the database."
  }

  assert {
    condition     = aws_iam_role_policy.migrate_execution_secrets.role == aws_iam_role.migrate_execution.id
    error_message = "This policy belongs on the migrate execution role."
  }
}

run "no_policy_grants_access_to_every_parameter" {
  assert {
    condition = alltrue(flatten([
      for policy in [
        aws_iam_role_policy.api_execution_secrets.policy,
        aws_iam_role_policy.migrate_execution_secrets.policy,
        ] : [
        for s in jsondecode(policy).Statement : !contains(s.Resource, "*")
      ]
    ]))
    error_message = "Resource must always be an explicit ARN, never \"*\"."
  }
}

run "the_roles_can_only_be_assumed_by_ecs_tasks" {
  assert {
    condition = alltrue([
      for role in [
        aws_iam_role.api_execution.assume_role_policy,
        aws_iam_role.migrate_execution.assume_role_policy,
        aws_iam_role.task.assume_role_policy,
        ] : anytrue([
          for s in jsondecode(role).Statement :
          try(s.Principal.Service == "ecs-tasks.amazonaws.com", false)
      ])
    ])
    error_message = "All three roles must be assumable only by the ECS tasks service."
  }
}

run "the_execution_roles_can_pull_images_and_write_logs" {
  assert {
    condition = (
      aws_iam_role_policy_attachment.api_execution_managed.policy_arn ==
      "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
    )
    error_message = "The api execution role needs the managed ECS execution policy."
  }

  assert {
    condition = (
      aws_iam_role_policy_attachment.migrate_execution_managed.policy_arn ==
      "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
    )
    error_message = "The migrate execution role needs the managed ECS execution policy."
  }
}

# --- the registry --------------------------------------------------------------------------------

run "the_image_comes_from_the_registry_infra_cicd_publishes" {
  # Since P8a the repository is permanent and lives in infra/cicd; this root only reads its URL.
  assert {
    condition     = data.aws_ssm_parameter.ecr_repository_url.name == "/stock-analyst/demo/ecr_repository_url"
    error_message = "infra/cicd publishes the registry URL under exactly this name."
  }

  assert {
    condition     = output.ecr_repository_url == "123456789012.dkr.ecr.ap-south-1.amazonaws.com/stock-analyst-demo-backend"
    error_message = "The output must repeat the URL read from the parameter."
  }
}

# --- what is published ------------------------------------------------------------------------------

run "the_outputs_name_things_and_never_carry_a_secret" {
  assert {
    condition     = output.ssm_runtime_parameter_name == "/stock-analyst/demo/database_url"
    error_message = "The output publishes the parameter NAME, which is not a secret."
  }

  assert {
    condition     = output.ssm_migration_parameter_name == "/stock-analyst/demo/migration_database_url"
    error_message = "The output publishes the parameter NAME, which is not a secret."
  }

  # A connection URL contains the password. Nothing that looks like one may be published.
  assert {
    condition = alltrue([
      for value in [output.db_instance_endpoint, output.ecr_repository_url] :
      !strcontains(value, "://") && !strcontains(value, "@")
    ])
    error_message = "No output may look like a connection URL."
  }

  assert {
    condition     = output.task_role_arn == aws_iam_role.task.arn
    error_message = "The task role's ARN is published for the P7d task definition."
  }
}

# --- bad input is refused -----------------------------------------------------------------------------

run "a_password_version_below_one_is_rejected" {
  command = plan

  variables {
    db_password_version = 0
  }

  expect_failures = [var.db_password_version]
}
