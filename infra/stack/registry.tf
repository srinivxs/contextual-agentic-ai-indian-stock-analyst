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
