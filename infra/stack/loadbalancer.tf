# infra/stack/loadbalancer.tf
#
# The public entrance. An application load balancer is a reverse proxy AWS runs across the subnets
# it is given; it has its own DNS name and its own network interfaces inside the VPC.
#
# THIS IS THE FIRST RESOURCE THAT BILLS FOR EXISTING. $0.0239/hour, about $0.57 a day, whether or
# not a single task is running behind it. Destroying the stack is the only way to stop it.
#
# Two independent layers keep the origin private, and both are needed:
#   1. The security group (P7b) admits port 80 only from CloudFront's origin-facing prefix list.
#   2. The listener below refuses everything by default and forwards only requests carrying a
#      secret header, which CloudFront adds in P7e.
# Layer 1 alone is not enough, because anybody can create a CloudFront distribution and point it at
# this load balancer; their traffic would arrive from the very same prefix list.

resource "aws_lb" "main" {
  name               = "${local.name_prefix}-alb"
  load_balancer_type = "application"

  # Internet-facing because CloudFront reaches it from outside the VPC. It is restricted by the
  # security group and the header rule, not by being hidden.
  internal = false

  subnets         = aws_subnet.public[*].id
  security_groups = [aws_security_group.alb.id]

  # The stack is destroyed at the end of every session; protection would only get in the way.
  enable_deletion_protection = false

  # Reject requests with malformed headers rather than passing them to the application.
  drop_invalid_header_fields = true

  tags = {
    Name = "${local.name_prefix}-alb"
  }
}

# --- where matching requests go -------------------------------------------------------------------
#
# Target type "ip", because each Fargate task has its own private address on its own network
# interface. ECS registers and deregisters those addresses as tasks start and stop; nothing here
# names a task.
resource "aws_lb_target_group" "api" {
  name        = "${local.name_prefix}-api"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  # /api/healthz is deliberately dependency-free: it does not touch the database, so a database
  # problem does not pull the container out of service and start a restart loop. /api/readyz, which
  # does check the database, is not used here for exactly that reason.
  health_check {
    path                = "/api/healthz"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  # A single task, so there is nothing to drain. The default of 300 seconds would make every
  # deployment and destroy needlessly slow.
  deregistration_delay = 30

  tags = {
    Name = "${local.name_prefix}-api"
  }
}

# --- the listener: refuse by default ----------------------------------------------------------------
#
# Port 80 only. CloudFront terminates HTTPS at the edge and reaches the origin over HTTP inside AWS
# (ADR 004), so there is no certificate to manage here.
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  # Anything that does not match the rule below gets this, and never reaches a container.
  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      message_body = "Forbidden"
      status_code  = "403"
    }
  }
}

# The one way in. CloudFront adds this header to every request it forwards; nobody else knows it.
#
# The secret itself is minted and held by infra/edge (see edge.tf), not here, because this root is
# destroyed every night and the two ends must still agree in the morning. The value reaches the
# state file either way -- a listener-rule condition is ordinary configuration Terraform compares
# on every plan -- which is acceptable because the state is encrypted and this header is defence
# in depth behind the CloudFront prefix list, not a primary credential.
resource "aws_lb_listener_rule" "origin_verify" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 100

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }

  condition {
    http_header {
      http_header_name = "X-Origin-Verify"
      values           = [data.aws_ssm_parameter.origin_verify.value]
    }
  }
}
