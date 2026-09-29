# Optional AKS profile (off by default): Free control-plane tier, one small autoscaling system
# pool, OIDC issuer + workload identity so each pod federates to its own managed identity (same
# model as the Container Apps profile), KEDA, and Container Insights.
resource "azurerm_kubernetes_cluster" "this" {
  name                      = var.name
  resource_group_name       = var.resource_group_name
  location                  = var.location
  tags                      = var.tags
  dns_prefix                = var.dns_prefix
  sku_tier                  = "Free"
  oidc_issuer_enabled       = true
  workload_identity_enabled = true
  local_account_disabled    = true
  azure_policy_enabled      = true
  automatic_upgrade_channel = "patch"

  default_node_pool {
    name                 = "system"
    vm_size              = var.node_vm_size
    auto_scaling_enabled = true
    min_count            = 1
    max_count            = 3
    os_disk_type         = "Managed"
    max_pods             = 50
  }

  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    network_policy      = "azure"
  }

  key_vault_secrets_provider {
    secret_rotation_enabled = true
  }

  identity {
    type = "SystemAssigned"
  }

  azure_active_directory_role_based_access_control {
    azure_rbac_enabled = true
    tenant_id          = var.tenant_id
  }

  oms_agent {
    log_analytics_workspace_id = var.log_analytics_workspace_id
  }

  workload_autoscaler_profile {
    keda_enabled = true
  }
}
