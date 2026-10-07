# infra/stack/variables.tf
#
# The inputs this root module takes. Most have a `validation` block, which Terraform checks
# before it contacts AWS at all: the cheapest possible place to catch a mistake.

variable "region" {
  type        = string
  description = "The AWS region. Only ap-south-1 (Mumbai) is accepted."
  default     = "ap-south-1"

  validation {
    condition     = var.region == "ap-south-1"
    error_message = "Only ap-south-1 (Mumbai) is allowed: the whole project lives in one region."
  }
}

variable "allowed_account_id" {
  type        = string
  description = "The 12-digit AWS account ID Terraform is allowed to work in."

  # No default. The account ID is never written in this repository; it is supplied from the
  # environment as TF_VAR_allowed_account_id, so leaving it out is an error rather than a silent
  # fallback into some other account.
  validation {
    condition     = can(regex("^[0-9]{12}$", var.allowed_account_id))
    error_message = "allowed_account_id must be exactly 12 digits."
  }
}

variable "vpc_cidr" {
  type        = string
  description = "The private address range for the VPC. Must be a /16."
  default     = "10.0.0.0/16"

  # A /16 is required, not merely preferred: network.tf cuts this range into /24 subnets by adding
  # 8 bits. Give it a /28 and that arithmetic fails much later with a far less obvious message, so
  # the variable refuses it here.
  validation {
    condition     = can(cidrhost(var.vpc_cidr, 0)) && can(regex("/16$", var.vpc_cidr))
    error_message = "vpc_cidr must be a valid CIDR block ending in /16, for example 10.0.0.0/16."
  }
}

variable "desired_count" {
  type        = number
  description = "How many copies of the API to run. 0 means the stack exists but no compute bills."
  default     = 0

  # The load balancer bills whether or not anything runs, but Fargate does not, so an apply must
  # never start compute by itself. One is the maximum: the design is a single task (ADR 008), and a
  # typo should not be able to start ten of them.
  validation {
    condition     = var.desired_count >= 0 && var.desired_count <= 1
    error_message = "desired_count must be 0 or 1."
  }
}

variable "image_tag" {
  type        = string
  description = "Commit SHA of the backend image to run. Null, the default, runs the newest image CI pushed."
  default     = null

  # CI tags images with the full commit SHA and ECR tags are immutable, so nothing else can exist.
  # `latest` in particular is refused: it was the hand-pushed tag before P8b and is never pushed now.
  validation {
    condition     = var.image_tag == null || can(regex("^[0-9a-f]{40}$", var.image_tag))
    error_message = "image_tag is a full 40-character commit SHA, or leave it unset for the newest image."
  }
}

variable "app_env" {
  type        = string
  description = "APP_ENV for the container. production requires an https base URL."

  # CloudFront exists now (P7e1), so the browser-facing origin is https and this can finally be
  # production -- which is what switches the session cookie to the __Host- prefix. "test" and
  # "local" remain accepted for a deliberate downgrade while debugging.
  default = "production"
  validation {
    condition     = contains(["local", "test", "production"], var.app_env)
    error_message = "app_env must be local, test or production."
  }
}

variable "google_client_id" {
  type        = string
  description = "Google OAuth client ID. Not a secret: it travels in the browser's address bar."
}

variable "allowed_emails" {
  type        = string
  description = "Comma-separated Google account emails allowed to sign in. Read from ALLOWED_EMAILS in .env."

  # Required, never empty: Google lets any account through when an app asks only for basic sign-in,
  # so without this list anyone with the address could sign in and use the chat.
  validation {
    condition = (
      length(compact(split(",", replace(var.allowed_emails, " ", "")))) > 0 &&
      alltrue([
        for email in compact(split(",", replace(var.allowed_emails, " ", ""))) :
        can(regex("^[^@,]+@[^@,]+\\.[^@,]+$", email))
      ])
    )
    error_message = "allowed_emails must list at least one valid email, comma-separated."
  }
}

variable "google_client_secret" {
  type        = string
  description = "Google OAuth client secret, supplied as TF_VAR_google_client_secret."
  sensitive   = true
}

variable "log_retention_days" {
  type        = number
  description = "How long container logs are kept before CloudWatch deletes them."
  default     = 7

  # CloudWatch accepts only this set of values, and rejects anything else at apply time.
  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90], var.log_retention_days)
    error_message = "log_retention_days must be one of 1, 3, 5, 7, 14, 30, 60 or 90."
  }
}

variable "db_password_version" {
  type        = number
  description = "Bump this to rotate the database passwords."
  default     = 1

  # Write-only attributes are never stored, so Terraform has nothing to compare against and cannot
  # tell whether the value changed. This number is the signal: the secret is re-sent only when it
  # goes up. Both the database and the two parameters use the same one, so they can never disagree.
  validation {
    condition     = var.db_password_version >= 1
    error_message = "db_password_version must be 1 or greater."
  }
}

# --- go-live: what the application is allowed to do (GL) ----------------------------------------------
#
# Two switches rather than six, so the cost brief can say in one line what is on. Each sets the same
# environment variables in both containers (compute.tf), exactly as the Compose stack does.

variable "data_sources_enabled" {
  type        = bool
  description = "Fetch filings (BSE via screener.in links), daily BSE prices and the live RBI feed."
  default     = true
}

variable "ai_enabled" {
  type        = bool
  description = "Call Bedrock: search fingerprints, reading filings for facts and events, and the chat."
  default     = true
}

# The spending caps, in US$. Each is checked by the application against the spend recorded in the
# database, so on a fresh database (every session) it is a cap per session.
variable "extraction_budget_usd" {
  type        = number
  description = "The most reading filings may spend (a full read is about $0.50)."
  default     = 2

  validation {
    condition     = var.extraction_budget_usd >= 0 && var.extraction_budget_usd <= 10
    error_message = "extraction_budget_usd must be between 0 and 10."
  }
}

variable "chat_budget_usd" {
  type        = number
  description = "The most the chat may spend (a question costs a fraction of a cent)."
  default     = 1

  validation {
    condition     = var.chat_budget_usd >= 0 && var.chat_budget_usd <= 10
    error_message = "chat_budget_usd must be between 0 and 10."
  }
}
