# infra/stack/secrets.tf
#
# The two database connection URLs, and the passwords inside them.
#
# THE POINT OF THIS FILE: neither password is ever written down. Not in the repository, not in the
# Terraform state file, not in plan output, not in an output value. They are generated in memory,
# sent to AWS once, and forgotten.
#
# How that works, in two parts:
#
#   EPHEMERAL RESOURCES exist only for the duration of a single Terraform operation. Unlike an
#   ordinary resource they are never recorded in state, so `random_password` below leaves no trace.
#
#   WRITE-ONLY ATTRIBUTES (the `_wo` suffix, Terraform 1.11 and later) are transmitted to the
#   provider and then discarded. The ordinary `value` attribute of an SSM parameter is documented as
#   being stored in state in plain text; `value_wo` is not stored at all. Because nothing is stored,
#   Terraform cannot detect a change, so `value_wo_version` tells it when to re-send.
#
# The consequence worth understanding: nobody, including us, can read these passwords back out of
# Terraform. To rotate them, raise db_password_version. To see one, look in SSM with the right IAM
# permission, which is exactly the access control we wanted.

ephemeral "random_password" "db_master" {
  length = 32

  # Letters and digits only. RDS forbids several punctuation characters in a master password, and a
  # URL would need the rest escaped; 32 alphanumerics is plenty of entropy without either problem.
  special = false
}

ephemeral "random_password" "db_runtime" {
  length  = 32
  special = false
}

locals {
  # Fixed names. The IAM policies in identity.tf name these parameters exactly, so that the api role
  # can be granted one and refused the other.
  runtime_parameter_name   = "/stock-analyst/demo/database_url"
  migration_parameter_name = "/stock-analyst/demo/migration_database_url"

  # Built from the region and account rather than read back from the parameters' own arn attribute.
  # That keeps identity.tf independent of resource creation order, and it means the IAM tests
  # compare against real strings instead of values a mock invented.
  parameter_arn_prefix    = "arn:aws:ssm:${var.region}:${var.allowed_account_id}:parameter"
  runtime_parameter_arn   = "${local.parameter_arn_prefix}${local.runtime_parameter_name}"
  migration_parameter_arn = "${local.parameter_arn_prefix}${local.migration_parameter_name}"

  # ?ssl=require is not decoration: the parameter group sets rds.force_ssl = 1, so PostgreSQL will
  # refuse a connection that does not use TLS. Carrying it in the URL means the application needs no
  # special configuration. P9 must confirm SQLAlchemy's asyncpg dialect accepts it in this form; if
  # it does not, the alternative is connect_args on the engine, and this string changes.
  database_suffix = "${aws_db_instance.main.address}:${aws_db_instance.main.port}/${aws_db_instance.main.db_name}?ssl=require"
}

# --- the runtime URL ------------------------------------------------------------------------------
#
# Used by the API. stock_app is the no-DDL role that the first migration creates inside PostgreSQL
# (ADR 011), using the password stored here. The role does not exist yet; the parameter does, so
# that P9 has somewhere to read the password it must set.
resource "aws_ssm_parameter" "database_url" {
  name        = local.runtime_parameter_name
  description = "Connection URL for the no-DDL runtime role used by the API"

  # SecureString encrypts at rest with the AWS-managed SSM key, which costs nothing. Standard
  # parameters are free; Secrets Manager would be $0.40 per secret per month and would store JSON
  # that ECS cannot turn back into a connection URL.
  type = "SecureString"

  value_wo         = "postgresql+asyncpg://stock_app:${ephemeral.random_password.db_runtime.result}@${local.database_suffix}"
  value_wo_version = var.db_password_version

  tags = {
    Name = "${local.name_prefix}-database-url"
  }
}

# --- the admin URL -----------------------------------------------------------------------------------
#
# The RDS master user. Only the migration role may read this: the API's execution role is deliberately
# not granted it, so a compromised API container cannot obtain credentials that can reshape the
# schema. That is the boundary ADR 011 describes, enforced by IAM rather than by convention.
resource "aws_ssm_parameter" "migration_database_url" {
  name        = local.migration_parameter_name
  description = "Connection URL for the privileged migration role (the RDS master user)"
  type        = "SecureString"

  value_wo         = "postgresql+asyncpg://${aws_db_instance.main.username}:${ephemeral.random_password.db_master.result}@${local.database_suffix}"
  value_wo_version = var.db_password_version

  tags = {
    Name = "${local.name_prefix}-migration-database-url"
  }
}
