// Container Apps environment on the Consumption workload profile (scale to zero, per-second billing).
param location string
param tags object
param resourceToken string
param logAnalyticsWorkspaceName string
@description('Infrastructure subnet id when the private networking profile is on; empty = public environment.')
param infrastructureSubnetId string = ''

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource env 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: 'cae-${resourceToken}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: { customerId: logs.properties.customerId, sharedKey: logs.listKeys().primarySharedKey }
    }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
    vnetConfiguration: empty(infrastructureSubnetId) ? null : { infrastructureSubnetId: infrastructureSubnetId, internal: true }
  }
}

output id string = env.id
output name string = env.name
output defaultDomain string = env.properties.defaultDomain
