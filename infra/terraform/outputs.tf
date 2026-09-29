# Same output names as infra/main.bicep.
output "AZURE_RESOURCE_GROUP" {
  value = azurerm_resource_group.this.name
}

output "AZURE_CONTAINER_REGISTRY_ENDPOINT" {
  value = module.registry.login_server
}

output "AZURE_CONTAINER_REGISTRY_NAME" {
  value = module.registry.name
}

output "AZURE_KEY_VAULT_NAME" {
  value = module.keyvault.name
}

output "APIM_GATEWAY_URL" {
  value = module.apim.gateway_url
}

output "FRONT_DOOR_HOST" {
  value = var.deploy_front_door ? module.frontdoor[0].endpoint_host_name : ""
}

output "SERVICE_BUS_NAMESPACE" {
  value = module.servicebus.fqdn
}

output "EVENT_GRID_TOPIC_ENDPOINT" {
  value = module.eventgrid.endpoint
}

output "FUNCTION_APP_NAME" {
  value = module.functions.name
}

output "APPLICATIONINSIGHTS_CONNECTION_STRING" {
  value     = module.monitoring.app_insights_connection_string
  sensitive = true
}

output "TOOL_GATEWAY_URL" {
  value = local.use_aca ? module.app["tool-gateway"].url : ""
}

output "WORKLOAD_IDENTITIES" {
  value = [for k in local.identity_keys : { workload = k, clientId = module.identity.client_ids[k], principalId = module.identity.principal_ids[k] }]
}
