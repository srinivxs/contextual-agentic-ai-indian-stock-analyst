# infra/stack/network.tf
#
# The private network everything else will sit in: one VPC, four subnets across two availability
# zones, and the routing that makes two of those subnets public and two of them unreachable from
# the internet in either direction.
#
# Everything in this file is free. A VPC, its subnets, route tables, associations and an internet
# gateway carry no hourly charge. The expensive network component, a NAT gateway, is deliberately
# absent (ADR 004/008).

# --- which availability zones exist ----------------------------------------------------------------
#
# Asking AWS rather than writing "ap-south-1a" and "ap-south-1b" by hand. Zone names are per-account
# labels, and a hard-coded one is the kind of thing that works until it doesn't.
data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  # Exactly two zones. An ALB and an RDS subnet group each require subnets in at least two, so two
  # is the minimum AWS will accept; more would cost nothing here but buys nothing either, since we
  # run a single task and a single-AZ database.
  azs = slice(data.aws_availability_zones.available.names, 0, 2)

  # Cutting the /16 into /24s by adding 8 bits: block 0 and block 1 are public.
  public_subnet_cidrs = [for index in [0, 1] : cidrsubnet(var.vpc_cidr, 8, index)]

  # The database side covers EVERY zone, not just two (changed in P8). RDS refused db.t4g.micro
  # twice in one afternoon for lack of capacity in the first two zones and named ap-south-1c as the
  # only one with room. A subnet group spanning all zones lets RDS place the instance wherever AWS has
  # capacity; subnets cost nothing. The first two keep the zones above, so adding more replaces nothing.
  db_azs = data.aws_availability_zones.available.names

  # Blocks 10, 11, 12... are isolated, one per zone. The gap after 1 is deliberate: room to add more
  # public subnets later without renumbering anything that already exists.
  isolated_subnet_cidrs = [for index in range(length(local.db_azs)) : cidrsubnet(var.vpc_cidr, 8, 10 + index)]
}

# --- the VPC ----------------------------------------------------------------------------------------
#
# A private slice of AWS's network, in one region, that belongs to this account. Nothing inside it
# is reachable from outside unless a path is deliberately built, which is what the rest of this file
# does for exactly two of the four subnets.
resource "aws_vpc" "main" {
  cidr_block = var.vpc_cidr

  # Both are needed for the application to reach the database. RDS gives out a hostname, not an
  # address; without these two the container cannot resolve it and the failure looks like an
  # unexplained connection timeout rather than a DNS problem.
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${local.name_prefix}-vpc"
  }
}

# --- the way out ------------------------------------------------------------------------------------
#
# An internet gateway is a managed component attached to the VPC. Attaching it exposes nothing by
# itself: a resource is reachable from the internet only when it has a public address AND its
# subnet's route table points here AND a security group allows the traffic. Three conditions.
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${local.name_prefix}-igw"
  }
}

# --- the subnets -------------------------------------------------------------------------------------
#
# A VPC spans a region, but each subnet lives in exactly one availability zone. The ONLY thing that
# makes one of these public and another isolated is the route table attached to it further down.

resource "aws_subnet" "public" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.public_subnet_cidrs[count.index]
  availability_zone = local.azs[count.index]

  # "Public" means "has a route to the internet gateway", not "everything here gets a public
  # address". The one task that needs an address asks for it explicitly in P7d.
  map_public_ip_on_launch = false

  tags = {
    Name = "${local.name_prefix}-public-${local.azs[count.index]}"
    Tier = "public"
  }
}

resource "aws_subnet" "isolated" {
  count             = length(local.db_azs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.isolated_subnet_cidrs[count.index]
  availability_zone = local.db_azs[count.index]

  tags = {
    Name = "${local.name_prefix}-isolated-${local.db_azs[count.index]}"
    Tier = "isolated"
  }
}

# --- routing ------------------------------------------------------------------------------------------
#
# A route table is a list of "for destination X, send it to Y" rules, and every subnet is associated
# with exactly one. Every table also has an undeletable `local` route for the VPC's own range, which
# is why anything in the VPC can reach anything else at the routing level; security groups are what
# actually restrict that.

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${local.name_prefix}-public"
  }
}

# The one line that makes two subnets public: anything not inside the VPC goes to the gateway.
resource "aws_route" "public_internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.main.id
}

# Deliberately empty: it carries only the implicit local route, so the subnets attached to it have
# no path to or from the internet at all.
#
# Creating it explicitly matters. A subnet with no association silently falls back to the VPC's main
# route table, and any route added there later would quietly give the database a way out.
resource "aws_route_table" "isolated" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${local.name_prefix}-isolated"
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "isolated" {
  count          = length(aws_subnet.isolated)
  subnet_id      = aws_subnet.isolated[count.index].id
  route_table_id = aws_route_table.isolated.id
}
