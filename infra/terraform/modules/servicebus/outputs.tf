output "id" {
  value = azurerm_servicebus_namespace.this.id
}

output "name" {
  value = azurerm_servicebus_namespace.this.name
}

output "fqdn" {
  value = "${azurerm_servicebus_namespace.this.name}.servicebus.windows.net"
}

output "queue_ids" {
  value = { for k, q in azurerm_servicebus_queue.this : k => q.id }
}
