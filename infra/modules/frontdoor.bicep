// Optional Azure Front Door (Standard) in front of APIM: global entry, TLS, WAF attach point.
// Off by default because it adds a monthly base fee; see docs/cost-estimate.md.
param tags object
param resourceToken string
param originHostName string

resource profile 'Microsoft.Cdn/profiles@2024-02-01' = {
  name: 'afd-${resourceToken}'
  location: 'global'
  tags: tags
  sku: { name: 'Standard_AzureFrontDoor' }
}

resource endpoint 'Microsoft.Cdn/profiles/afdEndpoints@2024-02-01' = {
  parent: profile
  name: 'aiip-${resourceToken}'
  location: 'global'
  properties: { enabledState: 'Enabled' }
}

resource og 'Microsoft.Cdn/profiles/originGroups@2024-02-01' = {
  parent: profile
  name: 'apim'
  properties: {
    loadBalancingSettings: { sampleSize: 4, successfulSamplesRequired: 3 }
    healthProbeSettings: { probePath: '/status-0123456789abcdef', probeRequestType: 'GET', probeProtocol: 'Https', probeIntervalInSeconds: 120 }
  }
}

resource origin 'Microsoft.Cdn/profiles/originGroups/origins@2024-02-01' = {
  parent: og
  name: 'apim'
  properties: { hostName: originHostName, originHostHeader: originHostName, httpsPort: 443, priority: 1, weight: 1000 }
}

resource route 'Microsoft.Cdn/profiles/afdEndpoints/routes@2024-02-01' = {
  parent: endpoint
  name: 'all'
  properties: {
    originGroup: { id: og.id }
    supportedProtocols: ['Https']
    patternsToMatch: ['/*']
    forwardingProtocol: 'HttpsOnly'
    httpsRedirect: 'Enabled'
    linkToDefaultDomain: 'Enabled'
  }
  dependsOn: [origin]
}

output endpointHostName string = endpoint.properties.hostName
