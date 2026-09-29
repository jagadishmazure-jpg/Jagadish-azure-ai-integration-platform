// Durable Functions host for the vendor-invoice process on Flex Consumption (FC1, Python 3.13).
// Storage uses identity-based connections only (no account keys in app settings).
param location string
param tags object
param resourceToken string
param identityId string
param identityClientId string
param identityPrincipalId string
param appInsightsConnectionString string
param appSettings array = []
param maximumInstanceCount int = 40
param instanceMemoryMB int = 2048

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: 'stfn${resourceToken}'
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource deployContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobs
  name: 'deployments'
}

// Durable Functions (Azure Storage provider) needs blob, queue and table data access.
var roles = [
  'b7e6dc6d-f1e8-4753-8033-0f276bb0955b' // Storage Blob Data Owner
  '974c5e8b-45b9-4653-ba55-5f855dd0fb88' // Storage Queue Data Contributor
  '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3' // Storage Table Data Contributor
]

resource storageRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for r in roles: {
  scope: storage
  name: guid(storage.id, identityPrincipalId, r)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', r)
    principalId: identityPrincipalId
    principalType: 'ServicePrincipal'
  }
}]

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: 'plan-fn-${resourceToken}'
  location: location
  tags: tags
  kind: 'functionapp'
  sku: { name: 'FC1', tier: 'FlexConsumption' }
  properties: { reserved: true }
}

resource fn 'Microsoft.Web/sites@2024-04-01' = {
  name: 'func-bpm-${resourceToken}'
  location: location
  tags: union(tags, { 'azd-service-name': 'bpm-functions' })
  kind: 'functionapp,linux'
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}deployments'
          authentication: { type: 'UserAssignedIdentity', userAssignedIdentityResourceId: identityId }
        }
      }
      scaleAndConcurrency: { maximumInstanceCount: maximumInstanceCount, instanceMemoryMB: instanceMemoryMB }
      runtime: { name: 'python', version: '3.13' }
    }
    siteConfig: {
      appSettings: concat([
        { name: 'AzureWebJobsStorage__accountName', value: storage.name }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'AzureWebJobsStorage__clientId', value: identityClientId }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
        { name: 'APPLICATIONINSIGHTS_AUTHENTICATION_STRING', value: 'ClientId=${identityClientId};Authorization=AAD' }
        { name: 'AZURE_CLIENT_ID', value: identityClientId }
        { name: 'AIIP_MODE', value: 'azure' }
      ], appSettings)
    }
  }
  dependsOn: [storageRoles, deployContainer]
}

output name string = fn.name
output hostname string = fn.properties.defaultHostName
