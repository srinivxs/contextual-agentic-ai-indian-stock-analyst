# One property, in its own file because it needs its own fake AWS.
#
# WHY THIS IS NOT IN compute.tftest.hcl
#   The backend refuses to start with APP_ENV=production unless PUBLIC_BASE_URL is https, because
#   that mode switches the session cookie to the __Host- prefix and browsers accept that only over
#   HTTPS. A lifecycle precondition on the API task definition catches the mistake at PLAN time
#   rather than as a container that starts and immediately exits, minutes into an apply.
#
#   Proving it fires means planning with a base URL that is NOT https. Since P7e2 that URL comes
#   from the parameter infra/edge publishes rather than from a variable, so the only way to make it
#   http is to mock the data source differently -- and `mock_data` is file-level, not per-run. Hence
#   a second file with one run block in it.
#
# HOW TO RUN IT
#   cd infra/stack && terraform test
#
# WHY THIS IS RED TODAY
#   data.aws_ssm_parameter.public_base_url is not declared yet.

mock_provider "aws" {
  # The whole point of this file: a base URL the backend must refuse in production.
  mock_data "aws_ssm_parameter" {
    defaults = {
      value = "http://not-https.example.com"
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
  google_client_secret = "mock-client-secret"
}

run "production_without_an_https_base_url_is_refused_before_anything_is_built" {
  command = plan

  expect_failures = [aws_ecs_task_definition.api]
}
