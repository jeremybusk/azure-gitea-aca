output "backend_config" {
  description = "Values used by the root Terraform AzureRM backend."
  value = {
    resource_group_name  = azurerm_resource_group.state.name
    storage_account_name = azurerm_storage_account.state.name
    container_name       = azurerm_storage_container.state.name
    key                  = "azure-gitea-aca/prod/terraform.tfstate"
    use_azuread_auth     = true
  }
}

output "storage_account_id" {
  description = "Resource ID used when assigning additional state access."
  value       = azurerm_storage_account.state.id
}

output "github_actions_variables" {
  description = "Non-secret GitHub Actions repository variables used for Azure OIDC."
  value = {
    AZURE_CLIENT_ID          = azuread_application.github.client_id
    AZURE_TENANT_ID          = data.azurerm_client_config.current.tenant_id
    AZURE_SUBSCRIPTION_ID    = data.azurerm_client_config.current.subscription_id
    TF_STATE_RESOURCE_GROUP  = azurerm_resource_group.state.name
    TF_STATE_STORAGE_ACCOUNT = azurerm_storage_account.state.name
    TF_STATE_CONTAINER       = azurerm_storage_container.state.name
  }
}

output "github_federated_subject" {
  description = "Subject claim Azure accepts from GitHub Actions."
  value       = azuread_application_federated_identity_credential.github.subject
}

output "github_client_id" {
  description = "Client ID configured as the AZURE_CLIENT_ID GitHub variable."
  value       = azuread_application.github.client_id
}

output "github_tenant_id" {
  description = "Tenant ID configured as the AZURE_TENANT_ID GitHub variable."
  value       = data.azurerm_client_config.current.tenant_id
}
