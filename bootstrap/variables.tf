variable "subscription_id" {
  description = "Azure subscription in which to create the Terraform state resources."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F-]{36}$", trimspace(var.subscription_id)))
    error_message = "subscription_id must be an Azure subscription UUID."
  }
}

variable "location" {
  description = "Azure region for the Terraform state resources."
  type        = string
  default     = "eastus2"
}

variable "resource_group_name" {
  description = "Resource group containing the Terraform state storage account."
  type        = string
  default     = "rg-tfstate-prod-eastus2-001"
}

variable "storage_account_name" {
  description = "Globally unique storage account name used for Terraform state."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.storage_account_name))
    error_message = "storage_account_name must contain 3-24 lowercase letters or numbers."
  }
}

variable "container_name" {
  description = "Private blob container used for Terraform state files."
  type        = string
  default     = "tfstate"
}

variable "github_owner" {
  description = "GitHub user or organization that owns the repository."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$", var.github_owner))
    error_message = "github_owner must be a valid GitHub user or organization name."
  }
}

variable "github_repository" {
  description = "GitHub repository trusted by the federated identity."
  type        = string
  default     = "azure-gitea-aca"
}

variable "github_environment" {
  description = "GitHub environment trusted by the federated identity."
  type        = string
  default     = "azure"
}

variable "github_application_display_name" {
  description = "Display name of the Entra application used by GitHub Actions."
  type        = string
  default     = "gha-azure-gitea-aca"
}

variable "enable_delete_lock" {
  description = "Protect the state storage account from accidental deletion."
  type        = bool
  default     = true
}

variable "tags" {
  description = "Tags applied to the Terraform state resources."
  type        = map(string)
  default = {
    environment = "prod"
    managed-by  = "terraform"
    purpose     = "terraform-state"
  }
}
