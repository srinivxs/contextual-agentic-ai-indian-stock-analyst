# Offline tests for the P7b network: the foundation the application stack will sit on.
#
# HOW TO READ THIS FILE
#   `mock_provider "aws"` swaps the real AWS provider for a fake one with the same schema that talks
#   to nothing: no credentials, no network, no cost, nothing created. Terraform "applies" the
#   configuration against the fake, and each `assert` inspects the result.
#
#   The two `mock_data` blocks fix the values of the two lookups the configuration makes. A mock
#   invents values for anything AWS would normally decide, so without these the availability zone
#   names and the prefix list ID would be random and nothing could be compared against them.
#
# HOW TO RUN IT
#   terraform init -backend=false     providers only: no state, no S3, no credentials
#   terraform test
#
# WHAT THE IMPLEMENTATION MUST CALL THINGS (the contract, written before the implementation)
#   variables   region, allowed_account_id, vpc_cidr
#   data        aws_availability_zones.available
#               aws_ec2_managed_prefix_list.cloudfront_origin_facing
#   resources   aws_vpc.main                              aws_internet_gateway.main
#               aws_subnet.public      (two, count)       aws_subnet.isolated    (one per zone, count)
#               aws_route_table.public                    aws_route_table.isolated
#               aws_route.public_internet
#               aws_route_table_association.public   (two)
#               aws_route_table_association.isolated (two)
#               aws_default_security_group.default
#               aws_security_group.alb / .task / .rds
#               aws_vpc_security_group_ingress_rule.alb_from_cloudfront
#               aws_vpc_security_group_egress_rule.alb_to_task
#               aws_vpc_security_group_ingress_rule.task_from_alb
#               aws_vpc_security_group_egress_rule.task_to_rds
#               aws_vpc_security_group_egress_rule.task_to_internet_https
#               aws_vpc_security_group_egress_rule.task_to_vpc_dns
#               aws_vpc_security_group_ingress_rule.rds_from_task
#   outputs     vpc_id, public_subnet_ids, isolated_subnet_ids,
#               alb_security_group_id, task_security_group_id, rds_security_group_id
#
# WHY THESE TESTS ARE RED TODAY
#   Only versions.tf exists, so every name above is undeclared and Terraform reports "reference to
#   undeclared ...". That is the right reason to fail: the code is missing, not the test.
#
# WHAT THESE TESTS CANNOT PROVE
#   A mock talks to nothing, so nothing here shows that the S3 backend initialises, that S3-native
#   locking really blocks a second writer, that state lands in the bucket, that `destroy` leaves the
#   bootstrap bucket alone, or that a second clean cycle works. Those are the apply/destroy drill in
#   the implementation step. Absence is also untestable here (you cannot assert "no NAT gateway
#   exists"), so infra/tests/test_infra_hygiene.py carries those checks instead.

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

  # P7d added resources whose ARNs the provider validates as arguments elsewhere, so a plan fails
  # on the random strings a mock would otherwise invent. This file asserts nothing about them and
  # only needs the plan to complete; compute.tftest.hcl uses override_resource for the same
  # resources, because its assertions have to tell them apart.
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

variables {
  allowed_account_id   = "123456789012"
  google_client_id     = "mock-client-id.apps.googleusercontent.com"
  google_client_secret = "mock-google-client-secret"
  allowed_emails       = "owner@example.com,friend@example.org"
}

# --- the VPC itself ------------------------------------------------------------------------------

run "the_vpc_uses_the_configured_address_range_and_resolves_dns" {
  assert {
    condition     = aws_vpc.main.cidr_block == "10.0.0.0/16"
    error_message = "The VPC must use the vpc_cidr variable, whose default is 10.0.0.0/16."
  }

  # RDS hands out a hostname, not an IP address. Without both of these switched on, the application
  # container cannot resolve it, and the failure looks like a mysterious connection timeout.
  assert {
    condition     = aws_vpc.main.enable_dns_support == true
    error_message = "enable_dns_support must be true or nothing inside the VPC can resolve DNS."
  }

  assert {
    condition     = aws_vpc.main.enable_dns_hostnames == true
    error_message = "enable_dns_hostnames must be true or the RDS endpoint name will not resolve."
  }
}

# --- the subnets ----------------------------------------------------------------------------

run "the_subnets_carve_the_vpc_into_four_predictable_blocks" {
  assert {
    condition     = aws_subnet.public[0].cidr_block == "10.0.0.0/24"
    error_message = "The first public subnet must be 10.0.0.0/24."
  }

  assert {
    condition     = aws_subnet.public[1].cidr_block == "10.0.1.0/24"
    error_message = "The second public subnet must be 10.0.1.0/24."
  }

  # The gap between 1 and 10 is deliberate: room to add more public subnets later without
  # renumbering anything that already exists.
  assert {
    condition     = aws_subnet.isolated[0].cidr_block == "10.0.10.0/24"
    error_message = "The first isolated subnet must be 10.0.10.0/24."
  }

  assert {
    condition     = aws_subnet.isolated[1].cidr_block == "10.0.11.0/24"
    error_message = "The second isolated subnet must be 10.0.11.0/24."
  }

  assert {
    condition     = aws_subnet.isolated[2].cidr_block == "10.0.12.0/24"
    error_message = "The third isolated subnet must be 10.0.12.0/24."
  }
}

# ADDED IN P8 (the Gate A session). RDS refused db.t4g.micro twice: first "no Availability Zones with
# sufficient capacity", then "choose from these Availability Zones: ap-south-1c" -- while the subnet
# group covered only the first two zones. A database subnet in EVERY zone lets RDS place the
# instance wherever AWS has capacity. Subnets cost nothing. The first two keep their zones and
# ranges, so adding the third replaces nothing that already exists.
run "the_database_can_be_placed_in_every_availability_zone" {
  assert {
    condition     = length(aws_subnet.isolated) == 3
    error_message = "One isolated subnet per zone: Mumbai has three."
  }

  assert {
    condition = (
      toset(aws_subnet.isolated[*].availability_zone) ==
      toset(["ap-south-1a", "ap-south-1b", "ap-south-1c"])
    )
    error_message = "The isolated subnets must cover every available zone."
  }

  assert {
    condition = (
      aws_subnet.isolated[0].availability_zone == aws_subnet.public[0].availability_zone &&
      aws_subnet.isolated[1].availability_zone == aws_subnet.public[1].availability_zone
    )
    error_message = "The first two keep their zones, paired with the public subnets, so nothing is replaced."
  }

  assert {
    condition     = length(aws_subnet.public) == 2
    error_message = "The public side stays at two zones: the load balancer needs two, and more buys nothing."
  }
}

run "the_subnet_ranges_follow_the_vpc_range_when_it_changes" {
  variables {
    vpc_cidr = "172.16.0.0/16"
  }

  # Proves the subnet ranges are derived from vpc_cidr rather than typed in as literals, which is the
  # difference between one source of truth and four places to get it wrong.
  assert {
    condition     = aws_subnet.public[0].cidr_block == "172.16.0.0/24"
    error_message = "Public subnet ranges must be derived from vpc_cidr, not hard-coded."
  }

  assert {
    condition     = aws_subnet.isolated[1].cidr_block == "172.16.11.0/24"
    error_message = "Isolated subnet ranges must be derived from vpc_cidr, not hard-coded."
  }
}

run "the_subnets_are_spread_across_two_availability_zones" {
  assert {
    condition     = aws_subnet.public[0].availability_zone != aws_subnet.public[1].availability_zone
    error_message = "The two public subnets must be in different availability zones."
  }

  assert {
    condition     = aws_subnet.isolated[0].availability_zone != aws_subnet.isolated[1].availability_zone
    error_message = "The two isolated subnets must be in different availability zones."
  }

  # Public and isolated are paired per zone, so a zone failure takes out one matched pair rather
  # than leaving a load balancer node alive in a zone with no database subnet.
  assert {
    condition     = aws_subnet.public[0].availability_zone == aws_subnet.isolated[0].availability_zone
    error_message = "Subnet index 0 must be the same zone in both tiers."
  }
}

run "a_public_subnet_does_not_hand_out_public_addresses_by_itself" {
  # "Public" here means "has a route to the internet gateway", not "every network interface gets a
  # public IP". The one task that needs a public address asks for it explicitly in P7d.
  assert {
    condition     = aws_subnet.public[0].map_public_ip_on_launch == false
    error_message = "Set map_public_ip_on_launch = false explicitly; the task requests its own address."
  }
}

# --- routing --------------------------------------------------------------------------------------

run "only_the_public_route_table_reaches_the_internet" {
  assert {
    condition     = aws_route.public_internet.route_table_id == aws_route_table.public.id
    error_message = "The default route must belong to the PUBLIC route table."
  }

  assert {
    condition     = aws_route.public_internet.destination_cidr_block == "0.0.0.0/0"
    error_message = "The default route must be 0.0.0.0/0 (everything not inside the VPC)."
  }

  assert {
    condition     = aws_route.public_internet.gateway_id == aws_internet_gateway.main.id
    error_message = "The default route must point at the internet gateway."
  }
}

run "the_isolated_route_table_exists_and_belongs_to_this_vpc" {
  # An explicit, empty route table matters: if the isolated subnets were left unassociated they would
  # silently fall back to the VPC's main route table, and whatever route someone adds there later
  # would quietly give the database a way out.
  assert {
    condition     = aws_route_table.isolated.vpc_id == aws_vpc.main.id
    error_message = "The isolated route table must belong to this VPC."
  }
}

run "each_subnet_is_attached_to_the_route_table_it_belongs_to" {
  assert {
    condition = alltrue([
      for index in [0, 1] :
      aws_route_table_association.public[index].route_table_id == aws_route_table.public.id &&
      aws_route_table_association.public[index].subnet_id == aws_subnet.public[index].id
    ])
    error_message = "Both public subnets must be associated with the public route table."
  }

  assert {
    condition = length(aws_route_table_association.isolated) == length(aws_subnet.isolated) && alltrue([
      for index in range(length(aws_subnet.isolated)) :
      aws_route_table_association.isolated[index].route_table_id == aws_route_table.isolated.id &&
      aws_route_table_association.isolated[index].subnet_id == aws_subnet.isolated[index].id
    ])
    error_message = "Every isolated subnet must be associated with the isolated route table."
  }
}

# --- the trust chain: CloudFront -> ALB -> task -> database --------------------------------------

run "the_load_balancer_is_reachable_only_from_cloudfront" {
  assert {
    condition     = aws_vpc_security_group_ingress_rule.alb_from_cloudfront.security_group_id == aws_security_group.alb.id
    error_message = "The rule must be attached to the ALB security group."
  }

  # A managed prefix list is AWS's own, maintained list of the addresses CloudFront uses to reach an
  # origin. Referencing it means the ALB is not open to the whole internet, and we never maintain
  # the address list ourselves.
  assert {
    condition     = aws_vpc_security_group_ingress_rule.alb_from_cloudfront.prefix_list_id == data.aws_ec2_managed_prefix_list.cloudfront_origin_facing.id
    error_message = "Inbound to the ALB must come from the CloudFront origin-facing prefix list."
  }

  assert {
    condition     = aws_vpc_security_group_ingress_rule.alb_from_cloudfront.from_port == 80 && aws_vpc_security_group_ingress_rule.alb_from_cloudfront.to_port == 80
    error_message = "Only port 80 is open on the ALB (CloudFront terminates HTTPS; ADR 004)."
  }

  assert {
    condition     = aws_vpc_security_group_ingress_rule.alb_from_cloudfront.ip_protocol == "tcp"
    error_message = "The ALB rule must be TCP."
  }
}

run "the_application_accepts_traffic_only_from_the_load_balancer" {
  assert {
    condition     = aws_vpc_security_group_ingress_rule.task_from_alb.security_group_id == aws_security_group.task.id
    error_message = "The rule must be attached to the task security group."
  }

  # Referencing the ALB's security group, rather than an address range, is what makes this a chain:
  # membership of that group is the credential, and it stays correct when addresses change.
  assert {
    condition     = aws_vpc_security_group_ingress_rule.task_from_alb.referenced_security_group_id == aws_security_group.alb.id
    error_message = "The task must accept traffic only from the ALB's security group."
  }

  assert {
    condition     = aws_vpc_security_group_ingress_rule.task_from_alb.from_port == 8000 && aws_vpc_security_group_ingress_rule.task_from_alb.to_port == 8000
    error_message = "The API listens on 8000; no other port should be open to it."
  }
}

run "the_database_accepts_traffic_only_from_the_application" {
  assert {
    condition     = aws_vpc_security_group_ingress_rule.rds_from_task.security_group_id == aws_security_group.rds.id
    error_message = "The rule must be attached to the database security group."
  }

  assert {
    condition     = aws_vpc_security_group_ingress_rule.rds_from_task.referenced_security_group_id == aws_security_group.task.id
    error_message = "PostgreSQL must be reachable only from the task's security group."
  }

  assert {
    condition     = aws_vpc_security_group_ingress_rule.rds_from_task.from_port == 5432 && aws_vpc_security_group_ingress_rule.rds_from_task.to_port == 5432
    error_message = "Only the PostgreSQL port may be open to the database."
  }
}

run "traffic_is_allowed_out_only_where_the_design_says" {
  assert {
    condition     = aws_vpc_security_group_egress_rule.alb_to_task.referenced_security_group_id == aws_security_group.task.id
    error_message = "The ALB may only send traffic to the task's security group."
  }

  assert {
    condition     = aws_vpc_security_group_egress_rule.task_to_rds.referenced_security_group_id == aws_security_group.rds.id
    error_message = "The task's database egress must target the database security group."
  }

  # The task genuinely needs the public internet: Bedrock, ECR, Google's key endpoint and the RBI
  # feed all live there and there is no NAT gateway or VPC endpoint to reach them through. It is
  # restricted to HTTPS, which is the narrowest rule that still works.
  assert {
    condition     = aws_vpc_security_group_egress_rule.task_to_internet_https.cidr_ipv4 == "0.0.0.0/0" && aws_vpc_security_group_egress_rule.task_to_internet_https.from_port == 443
    error_message = "The task needs outbound HTTPS to 0.0.0.0/0 (Bedrock, ECR, Google, RBI)."
  }

  # ADDED DURING IMPLEMENTATION, not before it. Name resolution needs its own rule, and it must not
  # reach past the VPC's own range. Flagged in the report rather than slipped in quietly.
  assert {
    condition     = aws_vpc_security_group_egress_rule.task_to_vpc_dns.cidr_ipv4 == aws_vpc.main.cidr_block && aws_vpc_security_group_egress_rule.task_to_vpc_dns.from_port == 53
    error_message = "DNS egress must be UDP 53 and must not reach beyond the VPC's own range."
  }
}

# --- what the rest of the stack will build on ----------------------------------------------------

run "the_network_publishes_what_the_later_milestones_need" {
  assert {
    condition     = output.vpc_id == aws_vpc.main.id
    error_message = "Output vpc_id must be the VPC's id."
  }

  assert {
    condition     = length(output.public_subnet_ids) == 2 && length(output.isolated_subnet_ids) == 3
    error_message = "Two public subnets, and one isolated subnet per zone."
  }

  assert {
    condition = (
      output.alb_security_group_id == aws_security_group.alb.id &&
      output.task_security_group_id == aws_security_group.task.id &&
      output.rds_security_group_id == aws_security_group.rds.id
    )
    error_message = "The three security group outputs must match the three security groups."
  }
}

# --- bad input is refused before AWS is ever contacted --------------------------------------------

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

run "a_vpc_range_that_is_not_a_slash_sixteen_is_rejected" {
  command = plan

  variables {
    # Too small to be cut into /24s: the subnet arithmetic would fail with a much less obvious error
    # much later, so the variable refuses it up front.
    vpc_cidr = "10.0.0.0/28"
  }

  expect_failures = [var.vpc_cidr]
}
