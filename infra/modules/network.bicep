// Optional private networking profile (off by default): VNet with a Container Apps infrastructure
// subnet and a private-endpoint subnet, plus private endpoints and DNS zones for Key Vault and
// Service Bus and the Event Grid topic. Service Bus private endpoints require the Premium tier;
// main.bicep forces it. One NSG covers both subnets, matching infra/terraform/modules/network.
param location string
param tags object
param resourceToken string
param keyVaultId string
param serviceBusId string
param eventGridTopicId string

// One NSG for both subnets (default rules only; tighten per client policy), as in Terraform.
resource nsg 'Microsoft.Network/networkSecurityGroups@2024-01-01' = {
  name: 'nsg-vnet-${resourceToken}'
  location: location
  tags: tags
  properties: { securityRules: [] }
}

resource vnet 'Microsoft.Network/virtualNetworks@2024-01-01' = {
  name: 'vnet-${resourceToken}'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['10.40.0.0/16'] }
    subnets: [
      {
        name: 'aca-infra'
        properties: {
          addressPrefix: '10.40.0.0/23'
          networkSecurityGroup: { id: nsg.id }
          delegations: [{ name: 'aca', properties: { serviceName: 'Microsoft.App/environments' } }]
        }
      }
      {
        name: 'private-endpoints'
        properties: { addressPrefix: '10.40.4.0/24', networkSecurityGroup: { id: nsg.id }, privateEndpointNetworkPolicies: 'Disabled' }
      }
    ]
  }
}

var targets = [
  { name: 'kv', id: keyVaultId, group: 'vault', zone: 'privatelink.vaultcore.azure.net' }
  { name: 'sb', id: serviceBusId, group: 'namespace', zone: 'privatelink.servicebus.windows.net' }
  { name: 'evgt', id: eventGridTopicId, group: 'topic', zone: 'privatelink.eventgrid.azure.net' }
]

resource zones 'Microsoft.Network/privateDnsZones@2020-06-01' = [for t in targets: {
  name: t.zone
  location: 'global'
  tags: tags
}]

resource links 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = [for (t, i) in targets: {
  parent: zones[i]
  name: 'link-${resourceToken}'
  location: 'global'
  properties: { virtualNetwork: { id: vnet.id }, registrationEnabled: false }
}]

resource pes 'Microsoft.Network/privateEndpoints@2024-01-01' = [for t in targets: {
  name: 'pe-${t.name}-${resourceToken}'
  location: location
  tags: tags
  properties: {
    subnet: { id: '${vnet.id}/subnets/private-endpoints' }
    privateLinkServiceConnections: [{ name: t.name, properties: { privateLinkServiceId: t.id, groupIds: [t.group] } }]
  }
}]

resource dnsGroups 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-01-01' = [for (t, i) in targets: {
  parent: pes[i]
  name: 'default'
  properties: { privateDnsZoneConfigs: [{ name: t.name, properties: { privateDnsZoneId: zones[i].id } }] }
}]

output acaSubnetId string = '${vnet.id}/subnets/aca-infra'
