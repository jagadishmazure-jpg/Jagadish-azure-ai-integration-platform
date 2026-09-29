// Log Analytics (with a daily ingestion cap) + workspace-based Application Insights.
param location string
param tags object
param resourceToken string
@description('Daily ingestion cap in GB. -1 removes the cap.')
param dailyQuotaGb int = 1
param retentionInDays int = 30

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-${resourceToken}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: retentionInDays
    workspaceCapping: { dailyQuotaGb: dailyQuotaGb }
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-${resourceToken}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
    DisableLocalAuth: false
  }
}

output workspaceId string = logs.id
output workspaceName string = logs.name
output appInsightsId string = appInsights.id
output connectionString string = appInsights.properties.ConnectionString
