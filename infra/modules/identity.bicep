// One user-assigned managed identity per workload. Each identity is later federated to that
// workload's own Entra app registration (see docs/identity.md), so every agent, worker and
// gateway authenticates as itself; nothing shares a credential.
param location string
param tags object
param resourceToken string
param workloads array

resource ids 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = [for w in workloads: {
  name: 'id-${w}-${resourceToken}'
  location: location
  tags: tags
}]

output identities array = [for (w, i) in workloads: {
  workload: w
  id: ids[i].id
  clientId: ids[i].properties.clientId
  principalId: ids[i].properties.principalId
}]
