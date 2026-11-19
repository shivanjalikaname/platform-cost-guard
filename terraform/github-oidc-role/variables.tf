variable "github_repository" {
  description = "Repository allowed to assume the role, as owner/name"
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "Use the form owner/name, without wildcards."
  }
}

variable "allowed_refs" {
  description = "Git refs whose workflows may assume the role"
  type        = list(string)
  default     = ["refs/heads/main"]
}

variable "role_name" {
  description = "Name of the IAM role"
  type        = string
  default     = "cost-guard-reader"
}

variable "create_oidc_provider" {
  description = "Create the GitHub OIDC provider (false if the account already has one)"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags for created resources"
  type        = map(string)
  default     = { ManagedBy = "terraform", Purpose = "cost-guard" }
}
