// Container registry for the single platform image (every gateway, agent, MCP server and worker
// runs the same image with a different command). Admin user off; pulls use managed identity.
param location string
param tags object
@minLength(3)
param resourceToken string
param pullPrincipalIds array = []

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: 'cr${resourceToken}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

var acrPull = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')

resource pulls 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for pid in pullPrincipalIds: {
  scope: acr
  name: guid(acr.id, pid, acrPull)
  properties: { roleDefinitionId: acrPull, principalId: pid, principalType: 'ServicePrincipal' }
}]

output loginServer string = acr.properties.loginServer
output name string = acr.name
