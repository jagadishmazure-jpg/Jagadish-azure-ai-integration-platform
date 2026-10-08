# azure-ai-integration-platform: Terraform mirror of infra/main.bicep.
#   callers -> [Front Door, optional] -> APIM -> gateways on Container Apps
#   gateways -> MCP servers, A2A agents, event workers ; Event Grid -> Service Bus -> KEDA workers
#   Durable Functions (Flex) for BPM ; Key Vault for vendor credentials ; Log Analytics + App Insights
data "azurerm_client_config" "current" {}

module "naming" {
  source      = "./modules/naming"
  workload    = var.workload
  environment = var.environment
  location    = var.location
  instance    = var.instance
  suffix      = var.name_suffix
}

resource "azurerm_resource_group" "this" {
  name     = module.naming.resource_group
  location = var.location
  tags     = local.tags
}

module "monitoring" {
  source              = "./modules/monitoring"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  tags                = local.tags
  log_analytics_name  = module.naming.log_analytics
  app_insights_name   = module.naming.app_insights
  daily_quota_gb      = var.log_daily_quota_gb
}

module "identity" {
  source              = "./modules/identity"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  tags                = local.tags
  identities          = { for k in local.identity_keys : k => "id-${k}-${module.naming.base}" }
}

module "keyvault" {
  source                        = "./modules/keyvault"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = var.location
  tags                          = local.tags
  name                          = module.naming.key_vault
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  purge_protection_enabled      = var.key_vault_purge_protection
  public_network_access_enabled = local.public
  # only workloads that call vendors read secrets
  secret_reader_principal_ids = { for k in local.secret_readers : k => module.identity.principal_ids[k] }
}

module "registry" {
  source              = "./modules/registry"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  tags                = local.tags
  name                = module.naming.container_registry
  pull_principal_ids  = module.identity.principal_ids
}

module "servicebus" {
  source                        = "./modules/servicebus"
  resource_group_name           = azurerm_resource_group.this.name
  location                      = var.location
  tags                          = local.tags
  name                          = local.n["sbns"]
  sku                           = local.sb_sku
  queues                        = ["order-events", "shipment-events", "invoice-events", "completions"]
  max_delivery_count            = 3
  default_message_ttl           = "P2D"
  duplicate_detection           = false # dedup happens on the business key in the gateway + worker
  receiver_principal_ids        = { for k in keys(local.workers) : k => module.identity.principal_ids[k] }
  public_network_access_enabled = local.public
}

module "eventgrid" {
  source                   = "./modules/eventgrid"
  resource_group_name      = azurerm_resource_group.this.name
  location                 = var.location
  tags                     = local.tags
  name                     = local.n["evgt"]
  service_bus_namespace_id = module.servicebus.id
  queue_ids                = module.servicebus.queue_ids
  publisher_principal_ids  = { event-gateway = module.identity.principal_ids["event-gateway"] }
  # Private networking turns public access off; publishers use the private endpoint below.
  public_network_access_enabled = !var.private_networking
  routes = {
    order-created    = { event_type = "com.contoso.sap.salesorder.created.v1", queue = "order-events" }
    shipment-late    = { event_type = "com.contoso.sap.delivery.late.v1", queue = "shipment-events" }
    invoice-received = { event_type = "com.contoso.sap.invoice.received.v1", queue = "invoice-events" }
    order-triaged    = { event_type = "com.contoso.aiip.order.triaged.v1", queue = "completions" }
    late-handled     = { event_type = "com.contoso.aiip.delivery.late.handled.v1", queue = "completions" }
  }
}

module "network" {
  source              = "./modules/network"
  count               = var.private_networking ? 1 : 0
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  tags                = local.tags
  name                = module.naming.vnet
  dns_zones = {
    keyvault   = "privatelink.vaultcore.azure.net"
    servicebus = "privatelink.servicebus.windows.net"
    eventgrid  = "privatelink.eventgrid.azure.net"
  }
}

module "private_endpoint" {
  source   = "./modules/private-endpoint"
  for_each = var.private_networking ? { kv = { id = module.keyvault.id, group = "vault", zone = "keyvault" }, sb = { id = module.servicebus.id, group = "namespace", zone = "servicebus" }, evgt = { id = module.eventgrid.id, group = "topic", zone = "eventgrid" } } : {}

  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  tags                = local.tags
  name                = "pe-${each.key}-${module.naming.base}"
  subnet_id           = module.network[0].pe_subnet_id
  target_resource_id  = each.value.id
  group_id            = each.value.group
  dns_zone_ids        = [module.network[0].zone_ids[each.value.zone]]
}

module "aca_env" {
  source                     = "./modules/containerapps-env"
  count                      = local.use_aca ? 1 : 0
  resource_group_name        = azurerm_resource_group.this.name
  location                   = var.location
  tags                       = local.tags
  name                       = module.naming.container_apps_env
  log_analytics_workspace_id = module.monitoring.log_analytics_id
  infrastructure_subnet_id   = var.private_networking ? module.network[0].aca_subnet_id : ""
  internal                   = true
}

module "app" {
  source              = "./modules/containerapp"
  for_each            = local.use_aca ? local.http_apps : {}
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
  name                = each.key
  service_name        = each.key
  environment_id      = module.aca_env[0].id
  identity_id         = module.identity.ids[each.key]
  registry_server     = module.registry.login_server
  image               = var.container_image
  command             = each.value.command
  external            = each.value.external && !var.private_networking
  min_replicas        = var.min_replicas
  env = merge(local.common_env, {
    AZURE_CLIENT_ID            = module.identity.client_ids[each.key]
    AIIP_WORKLOAD_REGISTRATION = each.value.reg
  })

  depends_on = [module.registry]
}

module "worker" {
  source                = "./modules/containerapp"
  for_each              = local.use_aca ? local.workers : {}
  resource_group_name   = azurerm_resource_group.this.name
  tags                  = local.tags
  name                  = each.key
  service_name          = each.key
  kind                  = "worker"
  environment_id        = module.aca_env[0].id
  identity_id           = module.identity.ids[each.key]
  registry_server       = module.registry.login_server
  image                 = var.container_image
  command               = each.value.command
  queue_name            = each.value.queue
  service_bus_namespace = module.servicebus.name
  min_replicas          = 0
  max_replicas          = 5
  env = merge(local.common_env, {
    AZURE_CLIENT_ID            = module.identity.client_ids[each.key]
    AIIP_WORKLOAD_REGISTRATION = each.value.reg
    WORKER_QUEUE               = each.value.queue
  })

  depends_on = [module.registry]
}

module "aks" {
  source                     = "./modules/aks"
  count                      = local.use_aca ? 0 : 1
  resource_group_name        = azurerm_resource_group.this.name
  location                   = var.location
  tags                       = local.tags
  name                       = local.n["aks"]
  dns_prefix                 = "aiip-${module.naming.base}"
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  log_analytics_workspace_id = module.monitoring.log_analytics_id
}

module "functions" {
  source                         = "./modules/functions"
  resource_group_name            = azurerm_resource_group.this.name
  location                       = var.location
  tags                           = local.tags
  name                           = "func-bpm-${module.naming.base}${module.naming.suffix}"
  plan_name                      = "asp-bpm-${module.naming.base}"
  storage_account_name           = module.naming.storage_account
  identity_id                    = module.identity.ids["bpm-functions"]
  identity_client_id             = module.identity.client_ids["bpm-functions"]
  identity_principal_id          = module.identity.principal_ids["bpm-functions"]
  app_insights_connection_string = module.monitoring.app_insights_connection_string
  app_settings = {
    AIIP_WORKLOAD_REGISTRATION = "bpm-invoice-orchestrator"
    AZURE_TENANT_ID            = local.tenant_id
    AIIP_TOOL_GATEWAY_URL      = "https://tool-gateway.${local.public_dom}"
    AIIP_A2A_GATEWAY_URL       = "https://a2a-gateway.${local.public_dom}"
    AIIP_IDENTITY_URL          = "https://identity-gateway.internal.${local.public_dom}"
  }
}

module "apim" {
  source              = "./modules/apim"
  resource_group_name = azurerm_resource_group.this.name
  location            = var.location
  tags                = local.tags
  name                = local.n["apim"]
  sku                 = var.apim_sku
  publisher_email     = var.apim_publisher_email
  entra_tenant_id     = var.entra_tenant_id
  backends = {
    tools  = "https://tool-gateway.${local.public_dom}"
    mcp    = "https://mcp-gateway.${local.public_dom}"
    a2a    = "https://a2a-gateway.${local.public_dom}"
    events = "https://event-gateway.${local.public_dom}"
    bpm    = "https://${module.functions.hostname}/api"
  }
  audiences = {
    tools  = "api://aiip-tool-gateway"
    mcp    = "api://aiip-mcp-gateway"
    a2a    = "api://aiip-care-planner"
    events = "api://aiip-event-gateway"
    bpm    = "api://aiip-bpm-invoice-orchestrator"
  }
}

module "frontdoor" {
  source              = "./modules/frontdoor"
  count               = var.deploy_front_door ? 1 : 0
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.tags
  name                = local.n["afd"]
  endpoint_name       = "aiip-${module.naming.base}${module.naming.suffix}"
  origin_host_name    = module.apim.hostname
}

# ---- alerting, diagnostics and Defender for Cloud (written and tested offline; not deployed) ----
locals {
  # Spans from aiip.shared.telemetry carry integration.* keys (customDimensions in App Insights).
  result_class = "tostring(customDimensions[\"integration.result_class\"])"
}

module "alerts" {
  source                  = "./modules/alerts"
  count                   = var.enable_alerts ? 1 : 0
  resource_group_name     = azurerm_resource_group.this.name
  location                = var.location
  tags                    = local.tags
  name_suffix             = module.naming.base
  action_group_name       = "ag-${module.naming.base}"
  action_group_short_name = "aiip"
  alert_email             = var.alert_email
  log_analytics_id        = module.monitoring.log_analytics_id
  app_insights_id         = module.monitoring.app_insights_id
  metric_alerts = {
    sb-dead-letters  = { scope = module.servicebus.id, namespace = "Microsoft.ServiceBus/namespaces", metric = "DeadletteredMessages", aggregation = "Maximum", operator = "GreaterThan", threshold = 0, severity = 2, description = "Business events are dead-lettering after 3 deliveries" }
    evgt-dropped     = { scope = module.eventgrid.id, namespace = "Microsoft.EventGrid/topics", metric = "DroppedEventCount", aggregation = "Total", operator = "GreaterThan", threshold = 0, severity = 1, description = "Event Grid dropped SAP events after retries" }
    evgt-publish-err = { scope = module.eventgrid.id, namespace = "Microsoft.EventGrid/topics", metric = "PublishFailCount", aggregation = "Total", operator = "GreaterThan", threshold = 5, severity = 2, description = "Event gateway cannot publish to the topic" }
    kv-availability  = { scope = module.keyvault.id, namespace = "Microsoft.KeyVault/vaults", metric = "Availability", aggregation = "Average", operator = "LessThan", threshold = 99, severity = 1, description = "Key Vault availability below 99%" }
  }
  log_alerts = {
    failed-requests = { query = "requests | where success == false", threshold = 5, severity = 2, description = "More than 5 failed requests in 15 minutes" }
    exceptions      = { query = "exceptions", threshold = 10, severity = 3, description = "Exception spike in the gateways, agents or workers" }
    integration-err = { query = "dependencies | where isnotempty(${local.result_class}) and ${local.result_class} != \"ok\"", threshold = 10, severity = 2, description = "Calls to systems of record failing, including HTTP 200 business rejects" }
    authz-denies    = { query = "dependencies | where ${local.result_class} == \"authz_deny\"", threshold = 5, severity = 2, description = "Authorization denials spiking: misconfigured identity or probing" }
  }
  diagnostic_targets = {
    keyvault   = module.keyvault.id
    registry   = module.registry.id
    servicebus = module.servicebus.id
    eventgrid  = module.eventgrid.id
  }
}

module "defender" {
  source = "./modules/defender"
  count  = var.enable_defender ? 1 : 0
  plans  = var.defender_plans
}
