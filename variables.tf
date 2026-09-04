variable "subscription_id" {
  description = "Azure subscription ID. When null, the AzureRM provider uses the active Azure CLI or ARM_SUBSCRIPTION_ID context."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.subscription_id == null ||
      can(regex("^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", trimspace(var.subscription_id)))
    )
    error_message = "subscription_id must be an Azure subscription UUID."
  }
}

variable "location" {
  description = "Azure region in which to deploy Gitea."
  type        = string
  default     = "westus2"
}

variable "name_prefix" {
  description = "Lowercase prefix used for the resource group, Container Apps environment, and app."
  type        = string
  default     = "gitea"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,18}[a-z0-9]$", var.name_prefix))
    error_message = "name_prefix must be 3-20 characters, start with a letter, end with a letter or number, and contain only lowercase letters, numbers, and hyphens."
  }
}

variable "gitea_image" {
  description = "Pinned official Gitea image. Review Gitea release notes before changing versions."
  type        = string
  default     = "docker.gitea.com/gitea:1.27.3"

  validation {
    condition     = can(regex("^docker\\.gitea\\.com/gitea:[0-9]+\\.[0-9]+\\.[0-9]+$", trimspace(var.gitea_image)))
    error_message = "gitea_image must be an exact stable tag from docker.gitea.com/gitea, for example docker.gitea.com/gitea:1.27.3."
  }
}

variable "min_replicas" {
  description = "Minimum replicas. Keep 0 for scale-to-zero/free-grant use; set 1 only when always-on availability is worth the additional cost."
  type        = number
  default     = 0

  validation {
    condition     = contains([0, 1], var.min_replicas)
    error_message = "min_replicas must be 0 or 1."
  }
}

variable "storage_quota_gb" {
  description = "Maximum size in GiB of the standard Azure Files share. Standard shares bill for used capacity, not this quota."
  type        = number
  default     = 5

  validation {
    condition     = var.storage_quota_gb >= 1 && var.storage_quota_gb <= 100
    error_message = "storage_quota_gb must be between 1 and 100."
  }
}

variable "disable_registration" {
  description = "Disable public account registration. Create the first administrator with the documented Azure exec command."
  type        = bool
  default     = true
}

variable "custom_domain" {
  description = "Optional custom hostname, such as git.example.com. Leave null to use the free azurecontainerapps.io hostname."
  type        = string
  default     = null
  nullable    = true

  validation {
    condition = (
      var.custom_domain == null ||
      can(regex("^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,63}$", trimspace(var.custom_domain)))
    )
    error_message = "custom_domain must be null or a lowercase fully qualified domain name."
  }
}

variable "enable_custom_domain" {
  description = "Create the ACA hostname binding only after the required public DNS records resolve."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Tags applied to Azure resources."
  type        = map(string)
  default = {
    application = "gitea"
    environment = "personal"
    managed-by  = "terraform"
  }
}
