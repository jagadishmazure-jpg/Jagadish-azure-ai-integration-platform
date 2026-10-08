output "id" {
  value = azurerm_eventgrid_topic.this.id
}

output "endpoint" {
  value = azurerm_eventgrid_topic.this.endpoint
}

output "public_network_access_enabled" {
  value = azurerm_eventgrid_topic.this.public_network_access_enabled
}
