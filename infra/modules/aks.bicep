// Optional AKS profile (off by default) for teams that must run the integration plane on
// Kubernetes: Free control-plane tier, one small system node pool, workload identity + OIDC issuer
// so each pod federates to its own managed identity exactly like the Container Apps profile.
param location string
param tags object
param resourceToken string
param nodeVmSize string = 'Standard_B2s'
param logAnalyticsWorkspaceId string

resource aks 'Microsoft.ContainerService/managedClusters@2024-09-01' = {
  name: 'aks-${resourceToken}'
  location: location
  tags: tags
  sku: { name: 'Base', tier: 'Free' }
  identity: { type: 'SystemAssigned' }
  properties: {
    dnsPrefix: 'aiip-${resourceToken}'
    agentPoolProfiles: [
      { name: 'system', mode: 'System', count: 1, vmSize: nodeVmSize, osType: 'Linux', enableAutoScaling: true, minCount: 1, maxCount: 3 }
    ]
    oidcIssuerProfile: { enabled: true }
    securityProfile: { workloadIdentity: { enabled: true } }
    addonProfiles: { omsagent: { enabled: true, config: { logAnalyticsWorkspaceResourceID: logAnalyticsWorkspaceId } } }
    workloadAutoScalerProfile: { keda: { enabled: true } }
  }
}

output oidcIssuerUrl string = aks.properties.oidcIssuerProfile.issuerURL
output name string = aks.name
