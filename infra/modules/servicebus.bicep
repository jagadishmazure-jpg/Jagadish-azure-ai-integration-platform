// Service Bus queues for event-driven agents. Basic tier is enough: queues, peek-lock, dead-letter
// sub-queues and MaxDeliveryCount all exist in Basic. Standard adds topics; Premium is required
// only for private endpoints.
param location string
param tags object
param resourceToken string
@allowed(['Basic', 'Standard', 'Premium'])
param sku string = 'Basic'
param queues array = ['order-events', 'shipment-events', 'invoice-events', 'completions']
param maxDeliveryCount int = 3
@description('Identities that receive + settle messages (event workers).')
param receiverPrincipalIds array = []
@description('Identities that send (Event Grid topic identity, event gateway in direct mode).')
param senderPrincipalIds array = []
param publicNetworkAccess string = 'Enabled'

resource ns 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: 'sb-${resourceToken}'
  location: location
  tags: tags
  sku: { name: sku, tier: sku, capacity: sku == 'Premium' ? 1 : null }
  properties: {
    disableLocalAuth: true
    minimumTlsVersion: '1.2'
    publicNetworkAccess: publicNetworkAccess
  }
}

resource q 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = [for name in queues: {
  parent: ns
  name: name
  properties: {
    maxDeliveryCount: maxDeliveryCount
    lockDuration: 'PT1M'
    deadLetteringOnMessageExpiration: true
    defaultMessageTimeToLive: 'P2D'
    requiresDuplicateDetection: false // Basic tier has no duplicate detection; dedup is done on the business key in the gateway + worker
  }
}]

var receiverRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4f6d3b9b-027b-4f4c-9142-0e5a2a2247e0')
var senderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39')

resource receivers 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in receiverPrincipalIds: {
  scope: ns
  name: guid(ns.id, pid, receiverRole)
  properties: { roleDefinitionId: receiverRole, principalId: pid, principalType: 'ServicePrincipal' }
}]

resource senders 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in senderPrincipalIds: {
  scope: ns
  name: guid(ns.id, pid, senderRole)
  properties: { roleDefinitionId: senderRole, principalId: pid, principalType: 'ServicePrincipal' }
}]

output id string = ns.id
output name string = ns.name
output fqdn string = '${ns.name}.servicebus.windows.net'
