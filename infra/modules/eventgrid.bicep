// Event Grid custom topic for canonical business events (CloudEvents 1.0). One subscription per
// event type routes to its Service Bus queue using the topic's managed identity. Local auth
// (access keys) is disabled: publishers use Entra ID (EventGrid Data Sender role).
param location string
param tags object
param resourceToken string
param serviceBusNamespaceId string
@description('eventType -> queue name')
param routes array = [
  { name: 'order-created', eventType: 'com.contoso.sap.salesorder.created.v1', queue: 'order-events' }
  { name: 'shipment-late', eventType: 'com.contoso.sap.delivery.late.v1', queue: 'shipment-events' }
  { name: 'invoice-received', eventType: 'com.contoso.sap.invoice.received.v1', queue: 'invoice-events' }
  { name: 'order-triaged', eventType: 'com.contoso.aiip.order.triaged.v1', queue: 'completions' }
  { name: 'late-handled', eventType: 'com.contoso.aiip.delivery.late.handled.v1', queue: 'completions' }
]
param publisherPrincipalIds array = []
@description('Disabled when the private networking profile is on (publishers reach the topic through its private endpoint)')
@allowed(['Enabled', 'Disabled'])
param publicNetworkAccess string = 'Enabled'

resource topic 'Microsoft.EventGrid/topics@2025-02-15' = {
  name: 'evgt-${resourceToken}'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    inputSchema: 'CloudEventSchemaV1_0'
    disableLocalAuth: true
    publicNetworkAccess: publicNetworkAccess
  }
}

var senderRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '69a216fc-b8fb-44d8-bc22-1f3c2cd27a39')
var egSender = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'd5a91429-5739-47e2-a06b-3470a27159e7')

resource sbNs 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' existing = {
  name: last(split(serviceBusNamespaceId, '/'))
}

resource topicToBus 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: sbNs
  name: guid(sbNs.id, topic.id, senderRole)
  properties: { roleDefinitionId: senderRole, principalId: topic.identity.principalId, principalType: 'ServicePrincipal' }
}

resource publishers 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in publisherPrincipalIds: {
  scope: topic
  name: guid(topic.id, pid, egSender)
  properties: { roleDefinitionId: egSender, principalId: pid, principalType: 'ServicePrincipal' }
}]

resource subs 'Microsoft.EventGrid/topics/eventSubscriptions@2025-02-15' = [for r in routes: {
  parent: topic
  name: r.name
  properties: {
    eventDeliverySchema: 'CloudEventSchemaV1_0'
    filter: { includedEventTypes: [r.eventType] }
    deliveryWithResourceIdentity: {
      identity: { type: 'SystemAssigned' }
      destination: {
        endpointType: 'ServiceBusQueue'
        properties: { resourceId: '${serviceBusNamespaceId}/queues/${r.queue}' }
      }
    }
    retryPolicy: { maxDeliveryAttempts: 10, eventTimeToLiveInMinutes: 1440 }
  }
  dependsOn: [topicToBus]
}]

output endpoint string = topic.properties.endpoint
output id string = topic.id
