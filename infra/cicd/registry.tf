# infra/cicd/registry.tf
#
# Where the backend container image lives. It moved here from infra/stack in P8a.
#
# WHY IT MOVED
#   GitHub builds and pushes an image on every push to main. infra/stack is destroyed after every
#   session, so for most of any day there would be no repository to push to, and every spin-up
#   started with an empty one that had to be filled by hand. Here it is permanent: CI always has
#   somewhere to push, and the stack pulls whatever is already there.
#
# Storage is $0.10 per GB-month. Five images of about 71 MB is roughly three and a half cents.

resource "aws_ecr_repository" "backend" {
  # The same name it had in infra/stack, so the task definitions and the push commands do not change.
  name = "${local.name_prefix}-backend"

  # Kept although this root is never destroyed in normal use: ECR refuses to delete a repository
  # that contains images, and if this root is ever retired, `terraform destroy` should just work.
  force_delete = true

  # Still mutable, because images are pushed by hand as `latest` until the P8b workflow exists.
  # Once CI tags every image with its commit SHA there is a real argument for IMMUTABLE; P8b
  # decides it.
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    # Basic scanning is free and reports known vulnerabilities in the image's packages.
    scan_on_push = true
  }

  tags = {
    Name = "${local.name_prefix}-backend"
  }
}

# Without a lifecycle policy every image ever pushed is kept and paid for. With CI pushing on every
# commit that matters far more than it did with hand pushes. Five is enough history to roll back to.
resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep only the five most recent images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 5
        }
        action = {
          type = "expire"
        }
      },
    ]
  })
}

# How infra/stack finds the repository: through SSM, in the same direction as the edge's values
# (persistent -> ephemeral), so the stack's data source can never point at something missing.
resource "aws_ssm_parameter" "ecr_repository_url" {
  name        = local.ecr_repository_url_parameter_name
  description = "Where the backend image lives; infra/stack builds its image references from it"

  # A registry URL is not a secret. SecureString would add a KMS call for nothing.
  type  = "String"
  value = aws_ecr_repository.backend.repository_url

  tags = {
    Name = "${local.name_prefix}-ecr-repository-url"
  }
}
