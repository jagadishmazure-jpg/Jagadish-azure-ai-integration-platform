# Service Bus namespace + queues, Entra ID only (SAS disabled). Duplicate detection needs
# Standard or Premium; consumers enforce idempotency keys regardless of tier.
resource "azurerm_servicebus_namespace" "this" {
  name                          = var.name
  resource_group_name           = var.resource_group_name
  location                      = var.location
  tags                          = var.tags
  sku                           = var.sku
  capacity                      = var.sku == "Premium" ? 1 : 0
  premium_messaging_partitions  = var.sku == "Premium" ? 1 : 0
  local_auth_enabled            = false
  minimum_tls_version           = "1.2"
  public_network_access_enabled = var.public_network_access_enabled

  identity {
    type = "SystemAssigned"
  }
}

resource "azurerm_servicebus_queue" "this" {
  for_each                             = toset(var.queues)
  name                                 = each.value
  namespace_id                         = azurerm_servicebus_namespace.this.id
  max_delivery_count                   = var.max_delivery_count
  lock_duration                        = "PT1M"
  dead_lettering_on_message_expiration = true
  default_message_ttl                  = var.default_message_ttl
  requires_duplicate_detection         = var.sku != "Basic" && var.duplicate_detection
}

resource "azurerm_role_assignment" "receiver" {
  for_each                         = var.receiver_principal_ids
  scope                            = azurerm_servicebus_namespace.this.id
  role_definition_name             = "Azure Service Bus Data Receiver"
  principal_id                     = each.value
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "sender" {
  for_each                         = var.sender_principal_ids
  scope                            = azurerm_servicebus_namespace.this.id
  role_definition_name             = "Azure Service Bus Data Sender"
  principal_id                     = each.value
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}
