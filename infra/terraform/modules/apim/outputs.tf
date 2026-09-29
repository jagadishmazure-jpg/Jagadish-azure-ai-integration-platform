output "name" {
  value = azurerm_api_management.this.name
}

output "gateway_url" {
  value = azurerm_api_management.this.gateway_url
}

output "hostname" {
  value = replace(azurerm_api_management.this.gateway_url, "https://", "")
}
