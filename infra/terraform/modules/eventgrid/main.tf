# Event Grid custom topic for canonical business events (CloudEvents 1.0). One subscription per
# event type routes to its Service Bus queue using the topic's managed identity. Access keys are
# disabled: publishers use Entra ID (EventGrid Data Sender).
resource "azurerm_eventgrid_topic" "this" {
  name                          = var.name
  resource_group_name           = var.resource_group_name
  location                      = var.location
  tags                          = var.tags
  input_schema                  = "CloudEventSchemaV1_0"
  local_auth_enabled            = false
  public_network_access_enabled = var.public_network_access_enabled

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_role_assignment" "topic_to_bus" {
  scope                            = var.service_bus_namespace_id
  role_definition_name             = "Azure Service Bus Data Sender"
  principal_id                     = azurerm_eventgrid_topic.this.identity[0].principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "publisher" {
  for_each                         = var.publisher_principal_ids
  scope                            = azurerm_eventgrid_topic.this.id
  role_definition_name             = "EventGrid Data Sender"
  principal_id                     = each.value
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_eventgrid_event_subscription" "route" {
  for_each              = var.routes
  name                  = each.key
  scope                 = azurerm_eventgrid_topic.this.id
  event_delivery_schema = "CloudEventSchemaV1_0"
  included_event_types  = [each.value.event_type]

  service_bus_queue_endpoint_id = var.queue_ids[each.value.queue]

  delivery_identity {
    type = "SystemAssigned"
  }

  retry_policy {
    max_delivery_attempts = 10
    event_time_to_live    = 1440
  }

  depends_on = [azurerm_role_assignment.topic_to_bus]
}
