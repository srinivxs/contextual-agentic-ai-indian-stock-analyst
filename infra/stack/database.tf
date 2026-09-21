# infra/stack/database.tf
#
# One PostgreSQL 16 instance, in the two isolated subnets built in P7b, reachable only from the
# application's security group and only over TLS.
#
# THIS IS WHERE THE MONEY STARTS. Running cost is about $0.021/hour for the instance plus
# $0.131/GB-month for the storage, so roughly $0.59 a day. Everything before P7c was free.
# `terraform destroy` at the end of a session takes it back to zero.

# --- where it is allowed to live -------------------------------------------------------------------
#
# RDS does not take a subnet, it takes a SUBNET GROUP: the set of subnets it may place the instance
# in. AWS insists on at least two availability zones even for a single-AZ instance, because it wants
# somewhere to fail over to should Multi-AZ ever be switched on. That requirement is the reason P7b
# built two isolated subnets rather than one.
#
# Only the isolated subnets appear here, so the database has no route to or from the internet.
resource "aws_db_subnet_group" "main" {
  name       = "${local.name_prefix}-db"
  subnet_ids = aws_subnet.isolated[*].id

  tags = {
    Name = "${local.name_prefix}-db"
  }
}

# --- refuse unencrypted connections -----------------------------------------------------------------
#
# rds.force_ssl = 1 makes PostgreSQL reject any connection that is not using TLS. It is a STATIC
# parameter, which is why apply_method is pending-reboot: AWS refuses "immediate" for static
# parameters, and a new instance picks the value up as it starts anyway.
#
# Exactly one parameter, deliberately. A parameter group is where unrelated tuning quietly collects.
resource "aws_db_parameter_group" "main" {
  name   = "${local.name_prefix}-pg16"
  family = "postgres16"

  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }

  tags = {
    Name = "${local.name_prefix}-pg16"
  }
}

# --- the instance ------------------------------------------------------------------------------------
resource "aws_db_instance" "main" {
  identifier = "${local.name_prefix}-db"
  engine     = "postgres"

  # Major version pinned, minor left to AWS. Naming a minor that does not exist in ap-south-1 is a
  # confusing failure, and we never established which minors this region offers. A change of MAJOR
  # version would be a different database, so a test asserts this stays exactly "16".
  engine_version = "16"
  instance_class = "db.t4g.micro"

  # gp3's minimum is 20 GB, which is far more than this project will ever use. Encryption uses the
  # free AWS-managed key.
  allocated_storage = 20
  storage_type      = "gp3"
  storage_encrypted = true

  # The same names the local Docker database uses (see .env.example), so nothing has to be
  # reconciled later: stock_admin is the privileged role, stock_app is the no-DDL runtime role that
  # the first migration creates inside the database.
  db_name  = "stock_analyst"
  username = "stock_admin"

  # The password is generated in memory and sent straight to AWS. `password_wo` is a write-only
  # attribute: the provider transmits it and then discards it, so it never reaches the state file.
  # The ordinary `password` attribute, which IS stored in state in plain text, is never set.
  password_wo         = ephemeral.random_password.db_master.result
  password_wo_version = var.db_password_version

  db_subnet_group_name   = aws_db_subnet_group.main.name
  parameter_group_name   = aws_db_parameter_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  # Multi-AZ doubles the bill for a demo that is destroyed every evening.
  multi_az            = false
  publicly_accessible = false

  # The three settings that make `terraform destroy` actually work. All three would be wrong in
  # production, which is the interesting part: deletion protection blocks the destroy outright, and
  # a final snapshot outlives the stack and keeps billing.
  backup_retention_period = 0
  skip_final_snapshot     = true
  deletion_protection     = false

  # Both cost money beyond their free allowance, and neither is useful here.
  performance_insights_enabled = false
  monitoring_interval          = 0

  # No waiting for a maintenance window on a throwaway database.
  apply_immediately          = true
  auto_minor_version_upgrade = true

  tags = {
    Name = "${local.name_prefix}-db"
  }
}
