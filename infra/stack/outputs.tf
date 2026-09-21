# infra/stack/outputs.tf
#
# What the network publishes. P7c and P7d are added to this same root module, so they will reference
# the resources directly rather than reading these; the outputs exist so that a human can check what
# was built with `terraform output`, and so the tests can assert on the shape of it.

output "vpc_id" {
  description = "The VPC everything in this stack lives in."
  value       = aws_vpc.main.id
}

output "vpc_cidr_block" {
  description = "The VPC's address range."
  value       = aws_vpc.main.cidr_block
}

output "availability_zones" {
  description = "The two availability zones the subnets are spread across."
  value       = local.azs
}

output "public_subnet_ids" {
  description = "Subnets with a route to the internet gateway. The load balancer and the task."
  value       = aws_subnet.public[*].id
}

output "isolated_subnet_ids" {
  description = "Subnets with no route out in either direction. The database."
  value       = aws_subnet.isolated[*].id
}

output "alb_security_group_id" {
  description = "Accepts HTTP only from the CloudFront prefix list."
  value       = aws_security_group.alb.id
}

output "task_security_group_id" {
  description = "Accepts 8000 only from the load balancer's group."
  value       = aws_security_group.task.id
}

output "rds_security_group_id" {
  description = "Accepts 5432 only from the task's group, and has no egress at all."
  value       = aws_security_group.rds.id
}
