# Offline plan tests: mocked providers, no Azure credentials, nothing created.
#   terraform init -backend=false && terraform test
mock_provider "azurerm" {
  mock_data "azurerm_client_config" {
    defaults = {
      tenant_id       = "00000000-0000-0000-0000-000000000001"
      subscription_id = "00000000-0000-0000-0000-000000000002"
      object_id       = "00000000-0000-0000-0000-000000000003"
    }
  }
}

run "dev_cost_min" {
  command = plan

  variables {
    environment = "dev"
  }

  assert {
    condition     = azurerm_resource_group.this.name == "rg-aiip-dev-eus2-001"
    error_message = "resource group must follow the CAF pattern"
  }

  assert {
    condition     = alltrue([for k in ["env", "owner", "project", "cost-center"] : contains(keys(azurerm_resource_group.this.tags), k)])
    error_message = "required tags missing"
  }

  assert {
    condition     = length(module.app) == 13 && length(module.worker) == 2 && length(module.aks) == 0
    error_message = "13 HTTP apps + 2 workers on Container Apps expected"
  }

  assert {
    condition     = length(module.identity.ids) == 16
    error_message = "one managed identity per workload (15) + the Functions host"
  }

  assert {
    condition     = length(module.frontdoor) == 0 && length(module.network) == 0 && local.sb_sku == "Basic"
    error_message = "dev must stay on the cost-min shape"
  }
}

run "prod_private_frontdoor" {
  command = plan

  variables {
    environment        = "prod"
    private_networking = true
    deploy_front_door  = true
    apim_sku           = "StandardV2"
  }

  assert {
    condition     = local.sb_sku == "Premium" && length(module.private_endpoint) == 3 && length(module.frontdoor) == 1
    error_message = "private networking forces Premium and adds 3 private endpoints (Key Vault, Service Bus, Event Grid)"
  }

  assert {
    condition     = module.eventgrid.public_network_access_enabled == false
    error_message = "private networking turns Event Grid public access off"
  }
}

run "aks_profile" {
  command = plan

  variables {
    environment     = "dev"
    compute_profile = "aks"
  }

  assert {
    condition     = length(module.aks) == 1 && length(module.app) == 0 && length(module.aca_env) == 0
    error_message = "aks profile swaps Container Apps for a cluster"
  }
}
