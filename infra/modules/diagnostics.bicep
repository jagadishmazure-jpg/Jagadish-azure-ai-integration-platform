// Diagnostic settings: allLogs + AllMetrics from Key Vault, the registry, Service Bus and Event Grid
// to Log Analytics. Mirrors the diagnostic_targets in infra/terraform/main.tf (module "alerts").
// Not deployed.
param logAnalyticsId string
param keyVaultName string
param registryName string
param serviceBusName string
param eventGridTopicName string

var settings = {
  workspaceId: logAnalyticsId
  logs: [{ categoryGroup: 'allLogs', enabled: true }]
  metrics: [{ category: 'AllMetrics', enabled: true }]
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = { name: keyVaultName }
resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = { name: registryName }
resource bus 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = { name: serviceBusName }
resource topic 'Microsoft.EventGrid/topics@2025-02-15' existing = { name: eventGridTopicName }

resource dKv 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = { name: 'diag-to-law', scope: kv, properties: settings }
resource dAcr 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = { name: 'diag-to-law', scope: acr, properties: settings }
resource dBus 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = { name: 'diag-to-law', scope: bus, properties: settings }
resource dTopic 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = { name: 'diag-to-law', scope: topic, properties: settings }

output targets array = ['keyvault', 'registry', 'servicebus', 'eventgrid']
