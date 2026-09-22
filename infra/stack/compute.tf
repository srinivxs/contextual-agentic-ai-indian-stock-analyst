# infra/stack/compute.tf
#
# Where the container actually runs. ECS has three layers that are easy to confuse:
#
#   CLUSTER          a namespace. On Fargate it is free and holds nothing.
#   TASK DEFINITION  an immutable, versioned blueprint: image, size, environment, secrets, logging,
#                    roles, CPU architecture. Registering one runs nothing.
#   SERVICE          keeps N copies of a task definition running and registered with the load
#                    balancer. A one-off job needs no service: you run the task definition directly.
#
# COST: the cluster, the task definitions and the service itself are free. Compute is billed only
# while a task runs: 0.25 vCPU and 0.5 GB is about $0.013/hour, plus $0.005/hour for the public
# address the task needs in the absence of a NAT gateway. desired_count defaults to 0 so applying
# this stack starts none of it.

# --- logs ---------------------------------------------------------------------------------------
#
# The awslogs driver will not create these, so they must exist first. CloudWatch charges to ingest
# and to store; retention keeps the stored half near zero.

resource "aws_cloudwatch_log_group" "api" {
  name              = "/stock-analyst/demo/api"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${local.name_prefix}-api"
  }
}

resource "aws_cloudwatch_log_group" "migrate" {
  name              = "/stock-analyst/demo/migrate"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${local.name_prefix}-migrate"
  }
}

resource "aws_cloudwatch_log_group" "provision" {
  name              = "/stock-analyst/demo/provision"
  retention_in_days = var.log_retention_days

  tags = {
    Name = "${local.name_prefix}-provision"
  }
}

resource "aws_ecs_cluster" "main" {
  name = local.name_prefix

  tags = {
    Name = local.name_prefix
  }
}

locals {
  container_image = "${aws_ecr_repository.backend.repository_url}:${var.image_tag}"

  # local.public_base_url is defined in edge.tf: it is the URL infra/edge published, not anything
  # this root can work out for itself.
}

# --- the API task ------------------------------------------------------------------------------------

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name_prefix}-api"
  requires_compatibilities = ["FARGATE"]

  # awsvpc gives the task its own network interface and private address, which is what allows the
  # task security group from P7b to apply to the task itself rather than to a host.
  network_mode = "awsvpc"

  # The smallest Fargate combination that exists.
  cpu    = "256"
  memory = "512"

  # Execution role: used by the ECS agent before the container starts, to pull the image and fetch
  # the secrets below. Task role: the identity the application code itself runs as.
  execution_role_arn = aws_iam_role.api_execution.arn
  task_role_arn      = aws_iam_role.task.arn

  # The image is built on Windows/amd64. A mismatch here fails at task start with a message about
  # the platform that is hard to interpret unless you know to look for it.
  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = local.container_image
      essential = true

      portMappings = [
        {
          containerPort = 8000
          protocol      = "tcp"
        },
      ]

      # Plain text, visible to anyone who can describe this task definition. Nothing secret here:
      # the Google client ID travels in the browser's address bar during sign-in anyway.
      environment = [
        { name = "APP_ENV", value = var.app_env },
        { name = "GOOGLE_CLIENT_ID", value = var.google_client_id },
        { name = "PUBLIC_BASE_URL", value = local.public_base_url },
      ]

      # Fetched from SSM by the EXECUTION role at start and injected as environment variables. The
      # admin database URL is absent, and the execution role could not read it anyway (P7c).
      secrets = [
        { name = "DATABASE_URL", valueFrom = local.runtime_parameter_arn },
        { name = "SESSION_SECRET", valueFrom = local.session_secret_arn },
        { name = "GOOGLE_CLIENT_SECRET", valueFrom = local.google_client_secret_arn },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.api.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "api"
        }
      }
    },
  ])

  # Caught at plan time rather than at task start. The backend refuses to run with APP_ENV set to
  # production unless the base URL is https, because that mode uses the __Host- cookie prefix which
  # browsers only accept over HTTPS. Without this, the mistake surfaces as a container that starts
  # and immediately exits, several minutes into an apply.
  lifecycle {
    precondition {
      condition     = var.app_env != "production" || startswith(local.public_base_url, "https://")
      error_message = "app_env = production needs an https public_base_url; set it to the CloudFront URL."
    }
  }

  tags = {
    Name = "${local.name_prefix}-api"
  }
}

# --- the migration task ---------------------------------------------------------------------------
#
# Not a service: it is run once, by hand for now and by the pipeline in P8, and it exits. It is the
# same image with a different command, and it is the only thing that gets the admin database URL.
resource "aws_ecs_task_definition" "migrate" {
  family                   = "${local.name_prefix}-migrate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.migrate_execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name      = "migrate"
      image     = local.container_image
      essential = true

      # The image has no ENTRYPOINT on purpose (backend/Dockerfile), so `command` replaces the
      # default uvicorn invocation outright. It must therefore be the real argv, not a shorthand:
      # ADR 014's "migrate" names the Compose SERVICE, and that service runs exactly this
      # (docker-compose.yml). alembic and alembic.ini are both in the image at /app.
      command = ["alembic", "upgrade", "head"]

      secrets = [
        { name = "MIGRATION_DATABASE_URL", valueFrom = local.migration_parameter_arn },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.migrate.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "migrate"
        }
      }
    },
  ])

  tags = {
    Name = "${local.name_prefix}-migrate"
  }
}

# --- the runtime-role bootstrap ---------------------------------------------------------------------
#
# The gap RDS leaves. ADR 011 gives the project two database roles: the migration role owns the
# schema, and the runtime role `stock_app` can read and write rows but cannot reshape anything.
# Locally, docker/postgres-init/01-roles-and-databases.sh creates the runtime role when the volume
# is first created. RDS has no equivalent hook -- it makes the database and the master user, and
# nothing else -- and Terraform cannot close the gap either, because the instance is in the isolated
# subnets and no `postgresql` provider outside the VPC can reach it. So the same SQL runs here, from
# inside the VPC, as a one-off task beside the migration.
#
# It reads the runtime password out of the runtime URL, because there is nowhere else to read it
# from: that password is generated ephemerally and stored write-only, so after the apply not even
# Terraform can recover it (secrets.tf). Both URLs therefore have to reach this container.
#
# It is idempotent and order-independent: see backend/src/app/db/provision.py.
resource "aws_ecs_task_definition" "provision" {
  family                   = "${local.name_prefix}-provision"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"

  # The other privileged one-off task. Its execution role already grants exactly the two parameters
  # this needs ("read-both-database-urls" in identity.tf), so nothing new is granted to anything and
  # the api execution role stays refused the admin URL.
  execution_role_arn = aws_iam_role.migrate_execution.arn
  task_role_arn      = aws_iam_role.task.arn

  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name      = "provision"
      image     = local.container_image
      essential = true

      # The real argv, for the same reason the migration task carries one: the image has no
      # ENTRYPOINT, so `command` replaces the uvicorn CMD outright, and a command the image cannot
      # run fails at task start rather than at plan time.
      command = ["python", "-m", "app.db.provision"]

      # The image keeps the code at /app/src and WORKDIR is /app. Uvicorn is told where to look with
      # `--app-dir src`; `python -m` has no such flag, so the import path is set here instead.
      environment = [
        { name = "PYTHONPATH", value = "/app/src" },
      ]

      # Both, and this is the only container that gets both: the admin URL to connect with, the
      # runtime URL to read the role name and password out of.
      secrets = [
        { name = "MIGRATION_DATABASE_URL", valueFrom = local.migration_parameter_arn },
        { name = "DATABASE_URL", valueFrom = local.runtime_parameter_arn },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.provision.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "provision"
        }
      }
    },
  ])

  tags = {
    Name = "${local.name_prefix}-provision"
  }
}

# --- the service -----------------------------------------------------------------------------------
#
# Keeps desired_count copies of the API task running and registered with the target group. At 0 it
# is a definition of intent that costs nothing; raising it to 1 is how the demo is switched on.
resource "aws_ecs_service" "api" {
  name            = "${local.name_prefix}-api"
  cluster         = aws_ecs_cluster.main.arn
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.desired_count
  launch_type     = "FARGATE"

  # There is no NAT gateway, so the task reaches ECR, Bedrock and Google by having its own public
  # address. Its protection is the task security group, not its position in the network
  # (ADR 004/008). The public subnets are the only ones with a route out.
  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  # Long enough for the container to boot before the load balancer starts failing it.
  health_check_grace_period_seconds = 60

  # If a deployment never becomes healthy, ECS stops and rolls back on its own instead of leaving
  # the service in a failing loop.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # The target group must be attached to a listener before the service may register with it.
  depends_on = [aws_lb_listener.http]

  tags = {
    Name = "${local.name_prefix}-api"
  }
}
