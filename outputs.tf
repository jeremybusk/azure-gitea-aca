output "gitea_url" {
  description = "Public HTTPS URL for Gitea."
  value       = var.custom_domain == null ? "https://${azurerm_container_app.gitea.ingress[0].fqdn}" : "https://${var.custom_domain}"
}

output "aca_url" {
  description = "Default Azure Container Apps URL, which remains useful for diagnostics when a custom domain is configured."
  value       = "https://${azurerm_container_app.gitea.ingress[0].fqdn}"
}

output "resource_group_name" {
  description = "Resource group containing the Gitea deployment."
  value       = azurerm_resource_group.this.name
}

output "container_app_name" {
  description = "Container App name used by Azure CLI administration commands."
  value       = azurerm_container_app.gitea.name
}

output "container_app_environment_name" {
  description = "Container Apps environment name."
  value       = azurerm_container_app_environment.this.name
}

output "storage_account_name" {
  description = "Storage account holding the persistent Gitea data share."
  value       = azurerm_storage_account.gitea.name
}

output "custom_domain_dns_records" {
  description = "DNS values needed before binding an optional custom domain. Null when custom_domain is not set."
  sensitive   = true
  value = var.custom_domain == null ? null : {
    hostname              = var.custom_domain
    aca_fqdn              = azurerm_container_app.gitea.ingress[0].fqdn
    environment_static_ip = azurerm_container_app_environment.this.static_ip_address
    verification_txt      = azurerm_container_app.gitea.custom_domain_verification_id
  }
}
