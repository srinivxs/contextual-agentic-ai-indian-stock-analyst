# Offline tests for P7d: the load balancer, the ECS cluster, the two task definitions and the
# service that keeps the API running.
#
# HOW TO READ THIS FILE
#   Same shape as the other two test files. `mock_provider "aws"` swaps in a fake AWS with the same
#   schema that talks to nothing, so the whole root module is planned against the fake and the
#   earlier milestones' mocks are repeated here. The `random` provider is not mocked: it works
#   locally and letting the real one run is a genuine check.
#
# HOW TO RUN IT
#   terraform test
#
# WHAT THE IMPLEMENTATION MUST CALL THINGS (the contract, written before the implementation)
#   variables   desired_count, google_client_id, google_client_secret,
#               log_retention_days, image_tag
#   ephemeral   random_password.session_secret
#   data        aws_ssm_parameter.origin_verify        published by infra/edge
#               aws_ssm_parameter.public_base_url      published by infra/edge
#   resources   (random_password.origin_verify MOVED to infra/edge in P7e2)
#               aws_cloudwatch_log_group.api / .migrate
#               aws_ssm_parameter.session_secret / .google_client_secret
#               aws_lb.main
#               aws_lb_target_group.api
#               aws_lb_listener.http
#               aws_lb_listener_rule.origin_verify
#               aws_ecs_cluster.main
#               aws_ecs_task_definition.api / .migrate / .provision
#               aws_ecs_service.api
#   outputs     alb_dns_name, ecs_cluster_name, api_task_definition_arn,
#               migrate_task_definition_arn, provision_task_definition_arn, ecs_service_name
#
# ADDED AFTER P7d (the runtime-role bootstrap)
#               aws_cloudwatch_log_group.provision
#               aws_ecs_task_definition.provision
#   RDS has no docker-entrypoint-initdb.d, so nothing creates the no-DDL runtime role that ADR 011
#   requires: the instance is only reachable from inside the VPC, so the SQL has to run there. This
#   is a one-off task like `migrate`, running `app.db.provision` from the same image.
#
# WHY THESE TESTS ARE RED TODAY
#   None of the names above are declared, so Terraform reports "reference to undeclared ...".
#
# WHAT THESE TESTS CANNOT PROVE
#   That a container actually starts, that the image architecture matches the host, or that the
#   health check passes. Those need a real apply: a task in RUNNING state and a target reported
#   healthy by the load balancer. Absence is checked in infra/tests/test_infra_hygiene.py.

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

# The registry URL infra/cicd publishes. mock_data above gives EVERY ssm parameter the same value,
# so this one is overridden by name, or the image would be built from the public URL.
override_data {
  target = data.aws_ssm_parameter.ecr_repository_url
  values = {
    value = "123456789012.dkr.ecr.ap-south-1.amazonaws.com/stock-analyst-demo-backend"
  }
}

# WHY override_resource AND NOT mock_resource FOR THESE
#   A mock invents a short random string for any computed attribute it is not given, and the AWS
#   provider validates that arguments like execution_role_arn actually look like ARNs, so a plan
#   fails before any assertion runs. `mock_resource` would fix that, but it sets one value for every
#   resource of a TYPE: all three IAM roles would share an ARN, and "the API task uses the API role"
#   would then pass even if it named the migration role. `override_resource` targets one resource
#   address, so each gets a distinct, valid ARN and the assertions keep their meaning.

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

variables {
  allowed_account_id   = "123456789012"
  google_client_id     = "mock-client-id.apps.googleusercontent.com"
  google_client_secret = "mock-google-client-secret"
}

# --- the load balancer ----------------------------------------------------------------------------

run "the_load_balancer_faces_the_internet_from_the_public_subnets" {
  assert {
    condition     = aws_lb.main.load_balancer_type == "application"
    error_message = "It must be an application load balancer: we route on an HTTP header."
  }

  # It has to be internet-facing, because CloudFront reaches it from outside the VPC. The
  # restriction is done by the security group and the header rule, not by hiding it.
  assert {
    condition     = aws_lb.main.internal == false
    error_message = "The load balancer must be internet-facing for CloudFront to reach it."
  }

  assert {
    condition = alltrue([
      for id in aws_subnet.public[*].id : contains(tolist(aws_lb.main.subnets), id)
    ])
    error_message = "The load balancer belongs in both public subnets."
  }

  assert {
    condition     = contains(tolist(aws_lb.main.security_groups), aws_security_group.alb.id)
    error_message = "The load balancer must use the alb security group from P7b."
  }
}

run "the_target_group_points_at_task_addresses_and_checks_health" {
  # Fargate tasks each get their own private address, so the targets are IPs rather than instances.
  # ECS registers and deregisters them as tasks start and stop.
  assert {
    condition     = aws_lb_target_group.api.target_type == "ip"
    error_message = "Fargate tasks are registered by IP address."
  }

  assert {
    condition     = aws_lb_target_group.api.port == 8000 && aws_lb_target_group.api.protocol == "HTTP"
    error_message = "The API listens on 8000 over plain HTTP inside the VPC."
  }

  # /api/healthz is the dependency-free liveness endpoint: it does not touch the database, so a
  # failing database does not take the container out of service.
  assert {
    condition     = aws_lb_target_group.api.health_check[0].path == "/api/healthz"
    error_message = "The health check must call /api/healthz, which has no dependencies."
  }

  assert {
    condition     = aws_lb_target_group.api.vpc_id == aws_vpc.main.id
    error_message = "The target group belongs to this VPC."
  }
}

run "anything_without_the_secret_header_is_refused_by_the_load_balancer" {
  assert {
    condition     = aws_lb_listener.http.port == 80 && aws_lb_listener.http.protocol == "HTTP"
    error_message = "One listener, port 80. CloudFront terminates HTTPS at the edge."
  }

  # Default-deny, implemented in the load balancer. Anything that does not match the rule below
  # gets a fixed 403 and never reaches a container.
  assert {
    condition     = aws_lb_listener.http.default_action[0].type == "fixed-response"
    error_message = "The default action must be a fixed response, not a forward."
  }

  assert {
    condition     = aws_lb_listener.http.default_action[0].fixed_response[0].status_code == "403"
    error_message = "The default response must be 403."
  }
}

run "only_a_request_carrying_the_shared_secret_is_forwarded" {
  # This is what stops somebody pointing THEIR CloudFront distribution at our load balancer: their
  # traffic arrives from the same prefix list, but without this header.
  #
  # `condition` and `http_header` are SETS in the provider schema, not lists, so they cannot be
  # indexed with [0]. Iterating is the correct way to read them, and it also means the assertion
  # does not depend on the order the provider happens to return.
  assert {
    condition = anytrue([
      for c in aws_lb_listener_rule.origin_verify.condition :
      anytrue([for h in c.http_header : h.http_header_name == "X-Origin-Verify"])
    ])
    error_message = "The rule must match on the X-Origin-Verify header."
  }

  assert {
    condition = anytrue([
      for c in aws_lb_listener_rule.origin_verify.condition :
      anytrue([
        for h in c.http_header :
        contains(tolist(h.values), data.aws_ssm_parameter.origin_verify.value)
      ])
    ])
    error_message = "The rule must match the secret the edge published, not one of its own."
  }

  assert {
    condition     = aws_lb_listener_rule.origin_verify.action[0].type == "forward"
    error_message = "A matching request is forwarded to the target group."
  }

  assert {
    condition     = aws_lb_listener_rule.origin_verify.action[0].target_group_arn == aws_lb_target_group.api.arn
    error_message = "It must forward to the API target group."
  }
}

# --- the task definitions -------------------------------------------------------------------------

run "the_api_task_runs_on_the_architecture_the_image_was_built_for" {
  # The image was built on Windows/amd64. A mismatch fails at task start with a message about the
  # platform, which is hard to interpret if you are not looking for it.
  assert {
    condition     = aws_ecs_task_definition.api.runtime_platform[0].cpu_architecture == "X86_64"
    error_message = "The task must be X86_64 to match the image."
  }

  assert {
    condition     = aws_ecs_task_definition.api.runtime_platform[0].operating_system_family == "LINUX"
    error_message = "The task runs Linux."
  }

  assert {
    condition     = contains(tolist(aws_ecs_task_definition.api.requires_compatibilities), "FARGATE")
    error_message = "Fargate, so there are no servers to manage."
  }

  # awsvpc gives the task its own network interface, which is what lets the P7b task security group
  # apply to the task itself.
  assert {
    condition     = aws_ecs_task_definition.api.network_mode == "awsvpc"
    error_message = "Fargate requires awsvpc networking."
  }

  assert {
    condition     = aws_ecs_task_definition.api.cpu == "256" && aws_ecs_task_definition.api.memory == "512"
    error_message = "The smallest Fargate size: 0.25 vCPU and 0.5 GB."
  }
}

run "the_two_task_definitions_use_the_execution_roles_they_are_entitled_to" {
  assert {
    condition     = aws_ecs_task_definition.api.execution_role_arn == aws_iam_role.api_execution.arn
    error_message = "The API task must use the api execution role, which cannot read the admin URL."
  }

  assert {
    condition     = aws_ecs_task_definition.api.task_role_arn == aws_iam_role.task.arn
    error_message = "The application code runs as the task role."
  }

  assert {
    condition     = aws_ecs_task_definition.migrate.execution_role_arn == aws_iam_role.migrate_execution.arn
    error_message = "The migration task must use the migrate execution role."
  }
}

run "the_api_container_is_given_the_runtime_url_and_never_the_admin_one" {
  # The IAM boundary from P7c would be pointless if the task definition simply asked for the admin
  # parameter: the task would fail to start, but the intent would be wrong. This asserts the intent.
  assert {
    condition = anytrue([
      for secret in jsondecode(aws_ecs_task_definition.api.container_definitions)[0].secrets :
      secret.valueFrom == "arn:aws:ssm:ap-south-1:123456789012:parameter/stock-analyst/demo/database_url"
    ])
    error_message = "The API container must receive the runtime database URL."
  }

  assert {
    condition = alltrue([
      for secret in jsondecode(aws_ecs_task_definition.api.container_definitions)[0].secrets :
      secret.valueFrom != "arn:aws:ssm:ap-south-1:123456789012:parameter/stock-analyst/demo/migration_database_url"
    ])
    error_message = "The API container must NEVER be given the admin database URL."
  }

  # The backend refuses to start without these, so they are not optional.
  assert {
    condition = alltrue([
      for name in ["DATABASE_URL", "GOOGLE_CLIENT_SECRET", "SESSION_SECRET"] :
      contains([
        for secret in jsondecode(aws_ecs_task_definition.api.container_definitions)[0].secrets : secret.name
      ], name)
    ])
    error_message = "The API needs DATABASE_URL, GOOGLE_CLIENT_SECRET and SESSION_SECRET injected."
  }

  # The client ID is not a secret: it travels in the browser's address bar during sign-in.
  assert {
    condition = alltrue([
      for name in ["GOOGLE_CLIENT_ID", "PUBLIC_BASE_URL", "APP_ENV", "COOKIE_SECURE"] :
      contains([
        for env in jsondecode(aws_ecs_task_definition.api.container_definitions)[0].environment : env.name
      ], name)
    ])
    error_message = "GOOGLE_CLIENT_ID, PUBLIC_BASE_URL, APP_ENV and COOKIE_SECURE belong in environment."
  }
}

run "the_api_container_serves_on_8000_and_logs_to_its_own_group" {
  assert {
    condition     = jsondecode(aws_ecs_task_definition.api.container_definitions)[0].portMappings[0].containerPort == 8000
    error_message = "The container exposes 8000, matching the target group."
  }

  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.api.container_definitions)[0].logConfiguration.options["awslogs-group"] ==
      aws_cloudwatch_log_group.api.name
    )
    error_message = "The API container must log to its own group."
  }

  assert {
    condition     = startswith(jsondecode(aws_ecs_task_definition.api.container_definitions)[0].image, "123456789012.dkr.ecr.ap-south-1.amazonaws.com/stock-analyst-demo-backend")
    error_message = "The image must come from this project's registry."
  }
}

run "the_migration_task_is_a_one_off_that_uses_the_admin_url" {
  assert {
    condition = anytrue([
      for secret in jsondecode(aws_ecs_task_definition.migrate.container_definitions)[0].secrets :
      secret.valueFrom == "arn:aws:ssm:ap-south-1:123456789012:parameter/stock-analyst/demo/migration_database_url"
    ])
    error_message = "The migration container needs the admin URL, which is the whole point of it."
  }

  # The backend image has NO ENTRYPOINT (backend/Dockerfile) — that is deliberate, so `command:`
  # can run a different program from the same image. There is therefore no `migrate` executable in
  # it: ADR 014's "migrate" is the name of the Compose SERVICE, and that service runs
  # `alembic upgrade head` (docker-compose.yml). Asserting the exact argv, not just that some word
  # appears in it, because an unrecognised command fails only at task start, minutes into an apply.
  assert {
    condition = jsondecode(aws_ecs_task_definition.migrate.container_definitions)[0].command == [
      "alembic", "upgrade", "head",
    ]
    error_message = "The migration container must run alembic upgrade head, the same argv Compose uses."
  }

  assert {
    condition     = aws_ecs_task_definition.migrate.runtime_platform[0].cpu_architecture == "X86_64"
    error_message = "Same architecture as the API task: it is the same image."
  }
}

# --- the runtime-role bootstrap ---------------------------------------------------------------------

run "the_provision_task_is_given_both_urls_because_it_creates_one_role_using_the_other" {
  # It reads the runtime password out of the runtime URL -- Terraform cannot pass it, because that
  # password is write-only and unreadable after the apply -- and applies it as the migration role.
  # The migrate execution role already grants exactly these two parameters ("read-both-database-urls"
  # in identity.tf), so this task needs no new IAM and the api role stays refused the admin URL.
  assert {
    condition = alltrue([
      for arn in [
        "arn:aws:ssm:ap-south-1:123456789012:parameter/stock-analyst/demo/migration_database_url",
        "arn:aws:ssm:ap-south-1:123456789012:parameter/stock-analyst/demo/database_url",
        ] : anytrue([
          for secret in jsondecode(aws_ecs_task_definition.provision.container_definitions)[0].secrets :
          secret.valueFrom == arn
      ])
    ])
    error_message = "The provision container needs the admin URL to connect and the runtime URL to read the password out of."
  }

  assert {
    condition     = aws_ecs_task_definition.provision.execution_role_arn == aws_iam_role.migrate_execution.arn
    error_message = "It is the other privileged one-off task, so it reuses that execution role rather than widening the api one."
  }
}

run "the_provision_task_runs_the_module_the_image_actually_contains" {
  # Exactly the mistake the migrate task shipped with: `command` replaces the image's CMD outright
  # (no ENTRYPOINT), and a command the image cannot run fails only at task start. Asserting the
  # exact argv.
  assert {
    condition = jsondecode(aws_ecs_task_definition.provision.container_definitions)[0].command == [
      "python", "-m", "app.db.provision",
    ]
    error_message = "The provision container must run python -m app.db.provision."
  }

  # The image keeps the code at /app/src and only uvicorn is told about it (`--app-dir src` in the
  # Dockerfile CMD). `python -m` needs the same directory on the import path, or it exits with
  # ModuleNotFoundError before it reaches a single line of ours.
  assert {
    condition = anytrue([
      for env in jsondecode(aws_ecs_task_definition.provision.container_definitions)[0].environment :
      env.name == "PYTHONPATH" && env.value == "/app/src"
    ])
    error_message = "Without PYTHONPATH=/app/src the module cannot be imported from WORKDIR /app."
  }

  assert {
    condition     = startswith(jsondecode(aws_ecs_task_definition.provision.container_definitions)[0].image, "123456789012.dkr.ecr.ap-south-1.amazonaws.com/stock-analyst-demo-backend")
    error_message = "The same image as the api and the migration: one build, three commands."
  }

  assert {
    condition     = aws_ecs_task_definition.provision.runtime_platform[0].cpu_architecture == "X86_64"
    error_message = "Same architecture as the other two tasks: it is the same image."
  }
}

run "the_provision_task_logs_where_its_own_failures_can_be_read" {
  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.provision.container_definitions)[0].logConfiguration.options["awslogs-group"] ==
      aws_cloudwatch_log_group.provision.name
    )
    error_message = "A one-off task that leaves no log is undebuggable: it has already exited."
  }

  assert {
    condition     = aws_cloudwatch_log_group.provision.retention_in_days == var.log_retention_days
    error_message = "Logs must expire on the same schedule as the rest, so nothing accumulates cost."
  }
}

# --- the service ------------------------------------------------------------------------------------

run "the_service_starts_switched_off" {
  # The load balancer bills whether or not anything runs, but Fargate does not. Zero is the default
  # so that applying the stack does not silently start paying for compute.
  assert {
    condition     = aws_ecs_service.api.desired_count == 0
    error_message = "desired_count must default to 0, so an apply does not start billing compute."
  }

  assert {
    condition     = aws_ecs_service.api.launch_type == "FARGATE"
    error_message = "Fargate, not EC2."
  }

  assert {
    condition     = aws_ecs_service.api.cluster == aws_ecs_cluster.main.arn
    error_message = "The service belongs to this project's cluster."
  }
}

run "the_service_runs_tasks_in_public_subnets_with_the_task_security_group" {
  # There is no NAT gateway, so the task reaches Bedrock, ECR and Google by having its own public
  # address. Its protection is the security group, not its position in the network (ADR 004/008).
  assert {
    condition     = aws_ecs_service.api.network_configuration[0].assign_public_ip == true
    error_message = "Without NAT, the task needs a public address to reach anything outside."
  }

  assert {
    condition = alltrue([
      for id in aws_subnet.public[*].id :
      contains(tolist(aws_ecs_service.api.network_configuration[0].subnets), id)
    ])
    error_message = "Tasks run in the public subnets, the only ones with a route out."
  }

  assert {
    condition = contains(
      tolist(aws_ecs_service.api.network_configuration[0].security_groups),
      aws_security_group.task.id
    )
    error_message = "The task must carry the task security group from P7b."
  }
}

run "the_service_registers_its_tasks_with_the_load_balancer" {
  # `load_balancer` is a set too, so it is iterated rather than indexed.
  assert {
    condition = anytrue([
      for lb in aws_ecs_service.api.load_balancer :
      lb.target_group_arn == aws_lb_target_group.api.arn && lb.container_port == 8000
    ])
    error_message = "The service must register tasks with the API target group on port 8000."
  }

  assert {
    condition = anytrue([
      for lb in aws_ecs_service.api.load_balancer : lb.container_name == "api"
    ])
    error_message = "The load balancer must target the container named api."
  }
}

# --- the new secrets and the logs ----------------------------------------------------------------------

run "the_app_secrets_follow_the_same_write_only_rule" {
  assert {
    condition = (
      aws_ssm_parameter.session_secret.type == "SecureString" &&
      aws_ssm_parameter.google_client_secret.type == "SecureString"
    )
    error_message = "Both app secrets must be SecureString."
  }

  # As in P7c: `value` is stored in state, `value_wo` is not. There is no assertion that `value` is
  # null, because that attribute is computed and a mock always invents one; the hygiene suite checks
  # the configuration text instead.
  assert {
    condition = (
      aws_ssm_parameter.session_secret.value_wo_version == var.db_password_version &&
      aws_ssm_parameter.google_client_secret.value_wo_version == var.db_password_version
    )
    error_message = "Both must re-send when the secret version changes."
  }
}

run "logs_do_not_accumulate_forever" {
  assert {
    condition = (
      aws_cloudwatch_log_group.api.retention_in_days == var.log_retention_days &&
      aws_cloudwatch_log_group.migrate.retention_in_days == var.log_retention_days
    )
    error_message = "Both log groups must expire their contents, or storage grows without limit."
  }
}

# --- bad input is refused -----------------------------------------------------------------------------

run "a_negative_task_count_is_rejected" {
  command = plan

  variables {
    desired_count = -1
  }

  expect_failures = [var.desired_count]
}

run "more_than_one_task_is_rejected" {
  command = plan

  # One task is the whole design (ADR 008). A typo that starts ten of them should not be possible.
  variables {
    desired_count = 10
  }

  expect_failures = [var.desired_count]
}

# --- P7e2: the seam with the persistent edge ------------------------------------------------------

run "the_application_calls_itself_by_the_name_the_edge_published" {
  # Before P7e2 this was the load balancer's own http name, so the cookie could never carry the
  # __Host- prefix. It now comes from the parameter infra/edge writes, so the container and
  # CloudFront cannot disagree about what the site is called.
  assert {
    condition = anytrue([
      for env in jsondecode(aws_ecs_task_definition.api.container_definitions)[0].environment :
      env.name == "PUBLIC_BASE_URL" && env.value == data.aws_ssm_parameter.public_base_url.value
    ])
    error_message = "PUBLIC_BASE_URL must be the value infra/edge published, not the load balancer's name."
  }

  assert {
    condition = anytrue([
      for env in jsondecode(aws_ecs_task_definition.api.container_definitions)[0].environment :
      env.name == "PUBLIC_BASE_URL" && startswith(env.value, "https://")
    ])
    error_message = "The browser-facing origin must be https, or the __Host- cookie is rejected."
  }
}

run "production_also_means_a_secure_cookie" {
  # Found by the first real production apply, not by any test: the container exited with
  # "COOKIE_SECURE must be true in production" and the service sat in a restart loop. The flag is
  # now derived from app_env rather than set separately, so it cannot be forgotten again.
  assert {
    condition = anytrue([
      for env in jsondecode(aws_ecs_task_definition.api.container_definitions)[0].environment :
      env.name == "COOKIE_SECURE" && env.value == "true"
    ])
    error_message = "production requires COOKIE_SECURE=true, or the backend refuses to start."
  }
}

run "the_application_runs_in_production_mode_by_default" {
  # The whole point of P7e: production switches the session cookie to the __Host- prefix, which
  # browsers accept only over HTTPS. Until CloudFront existed this had to default to "test".
  assert {
    condition     = var.app_env == "production"
    error_message = "app_env must now default to production; CloudFront exists."
  }

  assert {
    condition = anytrue([
      for env in jsondecode(aws_ecs_task_definition.api.container_definitions)[0].environment :
      env.name == "APP_ENV" && env.value == "production"
    ])
    error_message = "The container must actually be told production."
  }
}
