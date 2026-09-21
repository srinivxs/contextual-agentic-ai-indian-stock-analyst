# infra/stack/security.tf
#
# The trust chain. A security group is a stateful firewall attached to network interfaces, not to
# subnets. Stateful means an allowed request's reply is automatically allowed back: return rules are
# never written. Groups are default-deny and allow-only, so anything not described here is refused.
#
# The feature the whole design rests on is that a rule's source can be ANOTHER SECURITY GROUP rather
# than an address range. "Whatever is a member of the load balancer's group" stays correct when
# addresses change, when tasks restart and when the load balancer scales.
#
#   CloudFront --(prefix list)--> alb :80 --(alb's group)--> task :8000 --(task's group)--> rds :5432
#
# Rules are separate resources rather than inline ingress/egress blocks: one readable resource per
# rule, and the two styles conflict if mixed.
#
# Everything here is free. Security groups and their rules carry no charge.

# --- where CloudFront comes from ------------------------------------------------------------------
#
# A managed prefix list is AWS's own maintained list of the addresses CloudFront uses when it
# contacts an origin. Referencing it means the load balancer is never open to the whole internet and
# we never maintain an address list ourselves.
#
# WHY THIS IS HERE IN P7b, WHEN CLOUDFRONT IS NOT CREATED UNTIL P7e
#   This list is AWS infrastructure, not ours. It exists in every account whether or not we ever
#   create a distribution, so reading it creates no dependency on a later milestone.
#
#   The alternative was to give the load balancer's group no inbound rule until P7d. That was
#   rejected for two reasons. First, the whole point of building the security groups in P7b is to
#   prove the trust chain, and without this rule its first link does not exist. Second, a prefix list
#   counts against the rules-per-security-group quota by its maximum size, so it is the one part of
#   this design that can be refused on quota. Finding that out here, where every resource is free,
#   is far better than finding it out in P7d with a load balancer already billing.
data "aws_ec2_managed_prefix_list" "cloudfront_origin_facing" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

# --- the three groups ---------------------------------------------------------------------------------
#
# Terraform manages only the groups this project creates. The VPC also comes with a default security
# group, which we deliberately do NOT adopt: nothing is ever placed in it, it grants nothing to
# anything outside itself, and it is deleted along with the VPC. Managing it would mean Terraform
# modifying a resource it did not create, for no gain.

resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb"
  description = "Public load balancer: HTTP from CloudFront only"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${local.name_prefix}-alb"
  }
}

resource "aws_security_group" "task" {
  name        = "${local.name_prefix}-task"
  description = "Application task: inbound from the load balancer only"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${local.name_prefix}-task"
  }
}

resource "aws_security_group" "rds" {
  name        = "${local.name_prefix}-rds"
  description = "PostgreSQL: inbound from the application task only, no way out"
  vpc_id      = aws_vpc.main.id

  tags = {
    Name = "${local.name_prefix}-rds"
  }
}

# --- link 1: CloudFront -> the load balancer -----------------------------------------------------
#
# Port 80 only. CloudFront terminates HTTPS at the edge and reaches the origin over HTTP inside AWS
# (ADR 004). This is the network half of restricting the origin; the other half is a secret header
# checked by the listener rule in P7d, which is what stops somebody pointing THEIR OWN CloudFront
# distribution at this load balancer, since their traffic would arrive from this same prefix list.
resource "aws_vpc_security_group_ingress_rule" "alb_from_cloudfront" {
  security_group_id = aws_security_group.alb.id
  description       = "CloudFront edge locations reaching this origin"
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront_origin_facing.id
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_egress_rule" "alb_to_task" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Forward requests to the application task"
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

# --- link 2: the load balancer -> the task ---------------------------------------------------------

resource "aws_vpc_security_group_ingress_rule" "task_from_alb" {
  security_group_id            = aws_security_group.task.id
  description                  = "Only the load balancer may reach the API"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

# --- link 3: the task -> the database -----------------------------------------------------------------

resource "aws_vpc_security_group_egress_rule" "task_to_rds" {
  security_group_id            = aws_security_group.task.id
  description                  = "PostgreSQL"
  referenced_security_group_id = aws_security_group.rds.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_task" {
  security_group_id            = aws_security_group.rds.id
  description                  = "Only the application task may reach PostgreSQL"
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

# The database group has no egress rule at all, and that is the point: PostgreSQL can answer the
# task, because groups are stateful, but it can never open a connection to anything.

# --- the task's way out -------------------------------------------------------------------------------
#
# The task genuinely needs the public internet: Bedrock, ECR, Google's key endpoint and the RBI feed
# all live there, and with no NAT gateway and no VPC endpoints this is the only route to them.
# Restricting it to HTTPS is the narrowest rule that still works.
#
# Note the description below has no apostrophe. AWS allows only a-zA-Z0-9. _-:/()#,@[]+=&;{}!$* in
# a rule description and rejects anything else at APPLY time, which a mock provider never catches.
# "Google's" failed here once, with 22 of 23 resources already created; test_infra_hygiene.py now
# checks the character set offline.
resource "aws_vpc_security_group_egress_rule" "task_to_internet_https" {
  security_group_id = aws_security_group.task.id
  description       = "Bedrock, ECR, Google JWKS and the RBI feed"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# Name resolution, to the resolver AWS runs at the VPC's base address plus two. Sources disagree on
# whether security groups filter traffic to that resolver at all; this rule costs nothing, reaches
# no further than the VPC's own range, and removes a failure that would otherwise appear in P7d as
# an unexplained inability to reach anything by name.
resource "aws_vpc_security_group_egress_rule" "task_to_vpc_dns" {
  security_group_id = aws_security_group.task.id
  description       = "DNS to the VPC resolver"
  cidr_ipv4         = aws_vpc.main.cidr_block
  ip_protocol       = "udp"
  from_port         = 53
  to_port           = 53
}
