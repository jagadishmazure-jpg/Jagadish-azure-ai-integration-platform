// Key Vault in RBAC mode. The platform stores only vendor credentials here (Salesforce connected
// app key, SAP client secret, ServiceNow OAuth secret, Workday refresh token, Jira API token,
// warehouse PAT); code holds `kv://` references and resolves them at call time.
param location string
param tags object
param resourceToken string
@description('Principal ids that may read secrets (the gateways and MCP servers that call vendors).')
param secretReaderPrincipalIds array = []
param enablePurgeProtection bool = false
param publicNetworkAccess string = 'Enabled'

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: 'kv-${resourceToken}'
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: enablePurgeProtection ? true : null
    publicNetworkAccess: publicNetworkAccess
  }
}

var secretsUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')

resource readers 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in secretReaderPrincipalIds: {
  scope: kv
  name: guid(kv.id, pid, secretsUser)
  properties: { roleDefinitionId: secretsUser, principalId: pid, principalType: 'ServicePrincipal' }
}]

output id string = kv.id
output name string = kv.name
output uri string = kv.properties.vaultUri
