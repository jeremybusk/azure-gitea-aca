terraform {
  required_version = ">= 1.6.0"

  required_providers {
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.8"
    }

    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}

  subscription_id = trimspace(var.subscription_id)

  resource_provider_registrations = "none"
  resource_providers_to_register  = ["Microsoft.Storage"]
}

provider "azuread" {}
