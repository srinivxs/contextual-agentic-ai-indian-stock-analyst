# infra/cicd/outputs.tf
#
# Neither value is a secret. The role ARN is useless without a token GitHub mints only for this
# repository's main branch, which is why it can sit in the workflow file in plain text.

output "ecr_repository_url" {
  description = "Where CI (and, until P8b, the owner by hand) pushes the backend image."
  value       = aws_ecr_repository.backend.repository_url
}

output "ci_role_arn" {
  description = "The role the GitHub workflow assumes: role-to-assume in configure-aws-credentials."
  value       = aws_iam_role.ci.arn
}
