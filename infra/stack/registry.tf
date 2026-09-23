# infra/stack/registry.tf
#
# Where the backend image is pulled from. Since P8a this root no longer owns the repository:
# infra/cicd does, because CI pushes an image on every commit and this root is destroyed after every
# session, which would leave nowhere to push for most of the day.
#
# The URL arrives through SSM, the same way the edge's values do and for the same reason: the
# repository is permanent, so the parameter always exists, and the wiring runs persistent ->
# ephemeral. A plan of this root FAILS if infra/cicd has never been applied. That is intended.
data "aws_ssm_parameter" "ecr_repository_url" {
  name = "/stock-analyst/demo/ecr_repository_url"
}

locals {
  # The provider marks every SSM value sensitive, even a plain String one. A registry URL is not a
  # secret, and leaving it sensitive would hide every container definition in plan output.
  ecr_repository_url = nonsensitive(data.aws_ssm_parameter.ecr_repository_url.value)
}

# WHICH image runs (P8b). CI pushes one image per commit to main, tagged with the commit SHA, and
# the tags are immutable, so there is no `latest` to point at. By default this asks ECR for the
# newest image, which is the last commit whose checks all passed; `-var image_tag=<sha>` picks an
# older one instead, for example to go back to a known-good commit.
#
# The plan fails with "no matching image" while the repository is still empty. That is intended: CI
# fills it on the first push to main.
data "aws_ecr_image" "backend" {
  repository_name = "${local.name_prefix}-backend"
  image_tag       = var.image_tag
  most_recent     = var.image_tag == null ? true : null
}

locals {
  # By DIGEST, not by tag: the task definitions name the exact bytes that were planned, whatever is
  # pushed between plan and apply. All three tasks (api, migrate, provision) use this one reference.
  container_image = "${local.ecr_repository_url}@${data.aws_ecr_image.backend.image_digest}"
}
