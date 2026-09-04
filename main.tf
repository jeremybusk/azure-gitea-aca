locals {
  app_name             = "${var.name_prefix}-app"
  environment_name     = "${var.name_prefix}-env"
  storage_account_name = "${substr(replace(var.name_prefix, "-", ""), 0, 12)}data${random_string.storage_suffix.result}"
  configured_root_url  = var.custom_domain == null ? "" : "https://${var.custom_domain}/"
}

resource "random_string" "storage_suffix" {
  length  = 8
  upper   = false
  special = false
}

resource "random_password" "gitea_secret_key" {
  length  = 64
  special = false
}

resource "azurerm_resource_group" "this" {
  name     = "${var.name_prefix}-rg"
  location = var.location
  tags     = var.tags
}

resource "azurerm_storage_account" "gitea" {
  name                     = local.storage_account_name
  resource_group_name      = azurerm_resource_group.this.name
  location                 = azurerm_resource_group.this.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  account_kind             = "StorageV2"

  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  public_network_access_enabled   = true
  shared_access_key_enabled       = true
  allow_nested_items_to_be_public = false

  share_properties {
    retention_policy {
      days = 7
    }
  }

  tags = var.tags
}

resource "azurerm_storage_share" "gitea" {
  name               = "gitea-data"
  storage_account_id = azurerm_storage_account.gitea.id
  quota              = var.storage_quota_gb
  enabled_protocol   = "SMB"
}

resource "azurerm_container_app_environment" "this" {
  name                = local.environment_name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = var.tags

  # Streaming-only logs avoid a separately billed Log Analytics workspace.
  workload_profile {
    name                  = "Consumption"
    workload_profile_type = "Consumption"
  }
}

resource "azurerm_container_app_environment_storage" "gitea" {
  name                         = "gitea-data"
  container_app_environment_id = azurerm_container_app_environment.this.id
  account_name                 = azurerm_storage_account.gitea.name
  share_name                   = azurerm_storage_share.gitea.name
  access_key                   = azurerm_storage_account.gitea.primary_access_key
  access_mode                  = "ReadWrite"
}

resource "azurerm_container_app" "gitea" {
  name                         = local.app_name
  container_app_environment_id = azurerm_container_app_environment.this.id
  resource_group_name          = azurerm_resource_group.this.name
  revision_mode                = "Single"
  workload_profile_name        = "Consumption"
  tags                         = var.tags

  secret {
    name  = "gitea-secret-key"
    value = random_password.gitea_secret_key.result
  }

  ingress {
    external_enabled           = true
    allow_insecure_connections = false
    target_port                = 3000
    transport                  = "auto"

    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = 1

    volume {
      name         = "gitea-data"
      storage_name = azurerm_container_app_environment_storage.gitea.name
      storage_type = "AzureFile"

      # Azure Files uses SMB semantics. mfsymlinks supports Git symlinks, and
      # nobrl is safe here because max_replicas is deliberately fixed at one.
      mount_options = "uid=1000,gid=1000,dir_mode=0770,file_mode=0660,mfsymlinks,nobrl"
    }

    container {
      name   = "gitea"
      image  = var.gitea_image
      cpu    = 0.25
      memory = "0.5Gi"

      # Build the stable ACA URL at runtime. The environment DNS suffix is not
      # known while Terraform is constructing this resource.
      command = ["/bin/sh", "-c"]
      args = [<<-SHELL
        if [ -n "$${GITEA_ROOT_URL_OVERRIDE:-}" ]; then
          export ROOT_URL="$${GITEA_ROOT_URL_OVERRIDE}"
          export DOMAIN="$${GITEA_ROOT_URL_OVERRIDE#https://}"
          export DOMAIN="$${DOMAIN%/}"
        else
          export DOMAIN="$${CONTAINER_APP_NAME}.$${CONTAINER_APP_ENV_DNS_SUFFIX}"
          export ROOT_URL="https://$${DOMAIN}/"
        fi
        exec /usr/bin/entrypoint
      SHELL
      ]

      env {
        name        = "SECRET_KEY"
        secret_name = "gitea-secret-key"
      }

      env {
        name  = "INSTALL_LOCK"
        value = "true"
      }

      env {
        name  = "GITEA_ROOT_URL_OVERRIDE"
        value = local.configured_root_url
      }

      env {
        name  = "USER_UID"
        value = "1000"
      }

      env {
        name  = "USER_GID"
        value = "1000"
      }

      env {
        name  = "HTTP_PORT"
        value = "3000"
      }

      env {
        name  = "DISABLE_SSH"
        value = "true"
      }

      env {
        name  = "LFS_START_SERVER"
        value = "true"
      }

      env {
        name  = "DISABLE_REGISTRATION"
        value = tostring(var.disable_registration)
      }

      env {
        name  = "GITEA__server__PROTOCOL"
        value = "http"
      }

      env {
        name  = "GITEA__server__PUBLIC_URL_DETECTION"
        value = "auto"
      }

      env {
        name  = "GITEA__security__REVERSE_PROXY_LIMIT"
        value = "1"
      }

      env {
        name  = "GITEA__security__REVERSE_PROXY_TRUSTED_PROXIES"
        value = "*"
      }

      env {
        name  = "GITEA__database__DB_TYPE"
        value = "sqlite3"
      }

      env {
        name  = "GITEA__database__SQLITE_JOURNAL_MODE"
        value = "DELETE"
      }

      volume_mounts {
        name = "gitea-data"
        path = "/data"
      }

      startup_probe {
        transport               = "HTTP"
        port                    = 3000
        path                    = "/api/healthz"
        interval_seconds        = 5
        timeout                 = 3
        failure_count_threshold = 10
      }

      liveness_probe {
        transport               = "HTTP"
        port                    = 3000
        path                    = "/api/healthz"
        initial_delay           = 30
        interval_seconds        = 30
        timeout                 = 5
        failure_count_threshold = 3
      }

      readiness_probe {
        transport               = "HTTP"
        port                    = 3000
        path                    = "/api/healthz"
        interval_seconds        = 10
        timeout                 = 5
        failure_count_threshold = 3
        success_count_threshold = 1
      }
    }
  }

  depends_on = [azurerm_container_app_environment_storage.gitea]
}

resource "azurerm_container_app_custom_domain" "gitea" {
  count = var.custom_domain != null && var.enable_custom_domain ? 1 : 0

  name             = var.custom_domain
  container_app_id = azurerm_container_app.gitea.id

  # Certificate creation and binding is a one-time Azure CLI operation after
  # DNS validation. Do not have Terraform undo that binding on the next apply.
  lifecycle {
    ignore_changes = [
      certificate_binding_type,
      container_app_environment_certificate_id,
    ]
  }
}
