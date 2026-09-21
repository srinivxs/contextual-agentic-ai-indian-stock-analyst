# infra/stack/registry.tf
#
# Where the backend container image lives so ECS can pull it. One repository, in the destroyable
# stack rather than the bootstrap, so that `terraform destroy` genuinely leaves no application
# infrastructure behind. The cost of that choice is re-pushing the image after every full destroy.
#
# Storage is $0.10 per GB-month, so the image costs about two cents a month while it exists.

resource "aws_ecr_repository" "backend" {
  name = "${local.name_prefix}-backend"

  # Without this, `terraform destroy` fails: ECR refuses to delete a repository that still contains
  # images, and by the time we destroy there is always an image in it.
  force_delete = true

  # Mutable because images are pushed by hand until P8 automates it, and re-pushing a corrected
  # image under the same tag should not require a new tag. Once CI tags by commit SHA there is an
  # argument for making this immutable.
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    # Basic scanning is free and reports known vulnerabilities in the image's packages.
    scan_on_push = true
  }

  tags = {
    Name = "${local.name_prefix}-backend"
  }
}

# Without a lifecycle policy every image ever pushed is kept and paid for. Five is more than enough
# history to roll back to, and old ones disappear on their own.
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
