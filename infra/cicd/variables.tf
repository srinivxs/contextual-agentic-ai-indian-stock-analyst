# infra/cicd/variables.tf

variable "region" {
  type        = string
  description = "The one region this project uses. Only Mumbai is accepted."
  default     = "ap-south-1"

  # A typo here would build a second copy somewhere else and bill for it. The validation makes that
  # impossible rather than merely unlikely.
  validation {
    condition     = var.region == "ap-south-1"
    error_message = "This project runs only in ap-south-1 (Mumbai)."
  }
}

variable "allowed_account_id" {
  type        = string
  description = "The 12-digit AWS account this root may touch. Supplied as TF_VAR_allowed_account_id."

  validation {
    condition     = can(regex("^[0-9]{12}$", var.allowed_account_id))
    error_message = "An AWS account id is exactly 12 digits."
  }
}

variable "github_repository" {
  type        = string
  description = "The one GitHub repository allowed to assume the CI role, as owner/name."

  # This string becomes part of the trust policy's subject condition. A wrong value locks CI out,
  # which is harmless and obvious on the first run; a value with a wildcard in it would trust other
  # repositories, which is neither. The pattern allows exactly one owner and one name, and no `*`.
  default = "srinivxs/contextual-agentic-ai-indian-stock-analyst"

  validation {
    condition     = can(regex("^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$", var.github_repository))
    error_message = "Give the repository as owner/name, with no wildcards."
  }
}

# GitHub signs this repository's tokens with an IMMUTABLE subject (found on the first real run, P8b):
#   repo:<owner>@<owner id>/<name>@<repository id>:ref:refs/heads/main
# The ids never change, even if the repository is renamed, and a repository deleted and recreated
# under the same name gets a NEW id -- so a look-alike can never inherit this trust. Neither id is a
# secret: `gh api repos/<owner>/<name> --jq '[.owner.id,.id]'` prints both for anyone who can see it.

variable "github_owner_id" {
  type        = string
  description = "Numeric GitHub id of the repository's owner, part of the immutable OIDC subject."
  default     = "164909971"

  validation {
    condition     = can(regex("^[0-9]+$", var.github_owner_id))
    error_message = "github_owner_id is the owner's numeric id, digits only."
  }
}

variable "github_repository_id" {
  type        = string
  description = "Numeric GitHub id of the repository, part of the immutable OIDC subject."
  default     = "1377490279"

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "github_repository_id is the repository's numeric id, digits only."
  }
}
