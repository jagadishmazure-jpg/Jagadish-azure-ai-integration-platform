// API Management (Consumption) as the single front door for callers of the integration plane.
// It validates the Entra ID token (when a tenant is configured), strips spoofable identity headers,
// injects a traceparent if missing and applies a coarse rate limit. The gateways still validate the
// token themselves: APIM is defense in depth, not the only check.
param location string
param tags object
param resourceToken string
@allowed(['Consumption', 'Developer', 'BasicV2', 'StandardV2'])
param sku string = 'Consumption'
param publisherEmail string
param publisherName string = 'AI Integration Platform'
@description('name -> backend base URL, e.g. { tools: "https://tool-gateway..." }')
param backends object
param entraTenantId string = ''
@description('name -> expected audience (app ID URI) for that API')
param audiences object = {}

resource apim 'Microsoft.ApiManagement/service@2024-05-01' = {
  name: 'apim-${resourceToken}'
  location: location
  tags: tags
  sku: { name: sku, capacity: sku == 'Consumption' ? 0 : 1 }
  identity: { type: 'SystemAssigned' }
  properties: { publisherEmail: publisherEmail, publisherName: publisherName }
}

resource apis 'Microsoft.ApiManagement/service/apis@2024-05-01' = [for b in items(backends): {
  parent: apim
  name: b.key
  properties: {
    displayName: 'AI integration plane: ${b.key}'
    path: b.key
    protocols: ['https']
    serviceUrl: b.value
    subscriptionRequired: false
  }
}]

resource catchAll 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for (b, i) in items(backends): {
  parent: apis[i]
  name: 'all-post'
  properties: { displayName: 'POST passthrough', method: 'POST', urlTemplate: '/*' }
}]

resource catchAllGet 'Microsoft.ApiManagement/service/apis/operations@2024-05-01' = [for (b, i) in items(backends): {
  parent: apis[i]
  name: 'all-get'
  properties: { displayName: 'GET passthrough', method: 'GET', urlTemplate: '/*' }
}]

var strip = '<set-header name="x-caller-agent" exists-action="delete" /><set-header name="x-client-assertion" exists-action="delete" />'
var trace = '<set-header name="traceparent" exists-action="skip"><value>@($"00-{Guid.NewGuid().ToString("N")}-{Guid.NewGuid().ToString("N").Substring(0,16)}-01")</value></set-header>'

resource policies 'Microsoft.ApiManagement/service/apis/policies@2024-05-01' = [for (b, i) in items(backends): {
  parent: apis[i]
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: '<policies><inbound><base />${strip}${empty(entraTenantId) ? '' : '<validate-jwt header-name="Authorization" failed-validation-httpcode="401"><openid-config url="${environment().authentication.loginEndpoint}${entraTenantId}/v2.0/.well-known/openid-configuration" /><audiences><audience>${audiences[?b.key] ?? 'api://aiip-${b.key}'}</audience></audiences></validate-jwt>'}<rate-limit calls="120" renewal-period="60" />${trace}</inbound><backend><base /></backend><outbound><base /><set-header name="X-Powered-By" exists-action="delete" /></outbound><on-error><base /></on-error></policies>'
  }
}]

output name string = apim.name
output gatewayUrl string = apim.properties.gatewayUrl
output hostname string = replace(apim.properties.gatewayUrl, 'https://', '')
