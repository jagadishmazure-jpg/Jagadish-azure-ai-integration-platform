// Enterprise AI Integration Platform: `azd provision` entry point (nothing here is deployed by CI).
//
//   callers -> [Front Door, optional] -> APIM (Consumption) -> gateways on Container Apps
//   Tool / MCP / A2A / Event / Identity gateways -> MCP servers, A2A agents, event workers
//   Event Grid topic -> Service Bus queues -> KEDA-scaled workers ; Durable Functions (Flex) for BPM
//   Key Vault for vendor credentials ; Log Analytics (daily cap) + App Insights for everything
//
// Cost-minimized defaults: scale-to-zero Container Apps, Consumption APIM, Basic Service Bus,
// Flex Consumption Functions, Basic ACR, capped Log Analytics. Front Door, private networking and
// the AKS profile are opt-in.
targetScope = 'subscription'

@minLength(1)
@maxLength(64)
param environmentName string
@minLength(1)
param location string
@description('Entra tenant used for token validation in APIM and the gateways. Empty = validation is left to the gateways (dev only).')
param entraTenantId string = ''
param apimPublisherEmail string = 'noreply@example.com'

@allowed(['Consumption', 'Developer', 'BasicV2', 'StandardV2'])
param apimSku string = 'Consumption'
@allowed(['Basic', 'Standard', 'Premium'])
param serviceBusSku string = 'Basic'
@description('Log Analytics daily ingestion cap (GB). -1 = no cap.')
param logDailyQuotaGb int = 1
param minReplicas int = 0

@description('Opt-in: Azure Front Door (Standard) in front of APIM.')
param deployFrontDoor bool = false
@description('Opt-in: VNet + private endpoints (forces Service Bus Premium, internal Container Apps environment).')
param privateNetworking bool = false
@allowed(['containerapps', 'aks'])
@description('Compute for gateways/agents/workers. The AKS profile only provisions the cluster; workloads are then applied with the same image.')
param computeProfile string = 'containerapps'

var tags = { 'azd-env-name': environmentName, project: 'azure-ai-integration-platform' }
var resourceToken = toLower(uniqueString(subscription().id, environmentName, location))
var useAca = computeProfile == 'containerapps'
var effectiveSbSku = privateNetworking ? 'Premium' : serviceBusSku

// ------------------------------------------------------------------------------ workloads
// name: Container App name (also the logical service name in aiip.shared.http)
// reg:  the Entra app registration this workload authenticates as (aiip.identity.registrations)
var gateways = [
  { name: 'identity-gateway', reg: 'identity-gateway', external: false, command: ['python', '-m', 'uvicorn', 'aiip.identity.gateway:app', '--host', '0.0.0.0', '--port', '8080'] }
  { name: 'tool-gateway', reg: 'tool-gateway', external: true, command: ['python', '-m', 'uvicorn', 'aiip.tools.gateway:app', '--host', '0.0.0.0', '--port', '8080'] }
  { name: 'mcp-gateway', reg: 'mcp-gateway', external: true, command: ['python', '-m', 'uvicorn', 'aiip.mcp.gateway:app', '--host', '0.0.0.0', '--port', '8080'] }
  { name: 'a2a-gateway', reg: 'a2a-gateway', external: true, command: ['python', '-m', 'uvicorn', 'aiip.a2a.gateway:app', '--host', '0.0.0.0', '--port', '8080'] }
  { name: 'event-gateway', reg: 'event-gateway', external: true, command: ['python', '-m', 'uvicorn', 'aiip.events.gateway:app', '--host', '0.0.0.0', '--port', '8080'] }
]
var mcpServers = [
  { name: 'mcp-sap-orders', reg: 'mcp-gateway', external: false, command: ['python', '-m', 'aiip.mcp_servers', 'sap-orders'] }
  { name: 'mcp-servicenow-incidents', reg: 'mcp-gateway', external: false, command: ['python', '-m', 'aiip.mcp_servers', 'servicenow-incidents'] }
  { name: 'mcp-sql-warehouse', reg: 'mcp-gateway', external: false, command: ['python', '-m', 'aiip.mcp_servers', 'sql-warehouse'] }
]
var agents = [
  { name: 'agent-care-planner', reg: 'care-planner', external: false, command: ['python', '-m', 'aiip.agents', 'care-planner'] }
  { name: 'agent-crm', reg: 'crm-agent', external: false, command: ['python', '-m', 'aiip.agents', 'crm-agent'] }
  { name: 'agent-erp', reg: 'erp-agent', external: false, command: ['python', '-m', 'aiip.agents', 'erp-agent'] }
  { name: 'agent-data', reg: 'data-agent', external: false, command: ['python', '-m', 'aiip.agents', 'data-agent'] }
  { name: 'agent-ap-invoice', reg: 'ap-invoice-agent', external: false, command: ['python', '-m', 'aiip.agents', 'ap-invoice-agent'] }
]
var workers = [
  { name: 'worker-order-events', reg: 'worker-order-events', queue: 'order-events', command: ['python', '-m', 'aiip.events.worker', 'order-events'] }
  { name: 'worker-shipment-events', reg: 'worker-shipment-events', queue: 'shipment-events', command: ['python', '-m', 'aiip.events.worker', 'shipment-events'] }
]
var httpApps = concat(gateways, mcpServers, agents)
// identities: one per workload (MCP servers share the mcp-gateway registration's audience but get their own MI)
var identityNames = concat(map(httpApps, a => a.name), map(workers, w => w.name), ['bpm-functions'])

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module monitoring 'modules/monitoring.bicep' = {
  scope: rg
  name: 'monitoring'
  params: { location: location, tags: tags, resourceToken: resourceToken, dailyQuotaGb: logDailyQuotaGb }
}

module ids 'modules/identity.bicep' = {
  scope: rg
  name: 'identities'
  params: { location: location, tags: tags, resourceToken: resourceToken, workloads: identityNames }
}

var idList = ids.outputs.identities
var allPrincipals = map(idList, i => i.principalId)

module kv 'modules/keyvault.bicep' = {
  scope: rg
  name: 'keyvault'
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    // only workloads that call vendors read secrets: tool gateway, MCP servers, identity gateway
    secretReaderPrincipalIds: map(filter(idList, i => contains(['tool-gateway', 'identity-gateway', 'mcp-sap-orders', 'mcp-servicenow-incidents', 'mcp-sql-warehouse'], i.workload)), i => i.principalId)
    publicNetworkAccess: privateNetworking ? 'Disabled' : 'Enabled'
  }
}

module acr 'modules/registry.bicep' = {
  scope: rg
  name: 'registry'
  params: { location: location, tags: tags, resourceToken: resourceToken, pullPrincipalIds: allPrincipals }
}

module bus 'modules/servicebus.bicep' = {
  scope: rg
  name: 'servicebus'
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    sku: effectiveSbSku
    receiverPrincipalIds: map(filter(idList, i => startsWith(i.workload, 'worker-')), i => i.principalId)
    publicNetworkAccess: privateNetworking ? 'Disabled' : 'Enabled'
  }
}

module events 'modules/eventgrid.bicep' = {
  scope: rg
  name: 'eventgrid'
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    serviceBusNamespaceId: bus.outputs.id
    publisherPrincipalIds: map(filter(idList, i => i.workload == 'event-gateway'), i => i.principalId)
    publicNetworkAccess: privateNetworking ? 'Disabled' : 'Enabled'
  }
}

module network 'modules/network.bicep' = if (privateNetworking) {
  scope: rg
  name: 'network'
  params: { location: location, tags: tags, resourceToken: resourceToken, keyVaultId: kv.outputs.id, serviceBusId: bus.outputs.id, eventGridTopicId: events.outputs.id }
}

module caEnv 'modules/containerapps-env.bicep' = if (useAca) {
  scope: rg
  name: 'containerapps-env'
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    logAnalyticsWorkspaceName: monitoring.outputs.workspaceName
    infrastructureSubnetId: privateNetworking ? network!.outputs.acaSubnetId : ''
  }
}

// Service URLs: inside the environment apps reach each other by name over the env's internal DNS.
var serviceUrls = [for a in httpApps: { name: 'AIIP_${toUpper(replace(a.name, '-', '_'))}_URL', value: 'http://${a.name}' }]
var workloadReg = toObject(concat(httpApps, workers, [{ name: 'bpm-functions', reg: 'bpm-invoice-orchestrator' }]), a => a.name, a => a.reg)
// identity gateway (Azure mode) maps a caller's managed-identity principal id to its app registration
var miPrincipals = join(map(idList, i => '${i.principalId}=${workloadReg[i.workload]}'), ',')
var commonEnv = concat([
  { name: 'AIIP_MODE', value: 'azure' }
  { name: 'AZURE_TENANT_ID', value: empty(entraTenantId) ? subscription().tenantId : entraTenantId }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: monitoring.outputs.connectionString }
  { name: 'AIIP_KEYVAULT_NAME', value: kv.outputs.name }
  { name: 'AIIP_SERVICEBUS_NAMESPACE', value: bus.outputs.fqdn }
  { name: 'AIIP_EVENTGRID_TOPIC_ENDPOINT', value: events.outputs.endpoint }
  { name: 'AIIP_MI_PRINCIPALS', value: miPrincipals }
], serviceUrls)

module apps 'modules/containerapp.bicep' = [for (a, i) in httpApps: if (useAca) {
  scope: rg
  name: 'app-${a.name}'
  params: {
    location: location
    tags: tags
    name: a.name
    environmentId: caEnv!.outputs.id
    identityId: first(filter(idList, x => x.workload == a.name)).id
    identityClientId: first(filter(idList, x => x.workload == a.name)).clientId
    registryServer: acr.outputs.loginServer
    command: a.command
    external: a.external && !privateNetworking
    env: concat(commonEnv, [{ name: 'AIIP_WORKLOAD_REGISTRATION', value: a.reg }])
    minReplicas: minReplicas
  }
}]

module workerApps 'modules/containerapp.bicep' = [for w in workers: if (useAca) {
  scope: rg
  name: 'app-${w.name}'
  params: {
    location: location
    tags: tags
    name: w.name
    kind: 'worker'
    environmentId: caEnv!.outputs.id
    identityId: first(filter(idList, x => x.workload == w.name)).id
    identityClientId: first(filter(idList, x => x.workload == w.name)).clientId
    registryServer: acr.outputs.loginServer
    command: w.command
    queueName: w.queue
    serviceBusNamespace: bus.outputs.name
    env: concat(commonEnv, [{ name: 'AIIP_WORKLOAD_REGISTRATION', value: w.reg }, { name: 'WORKER_QUEUE', value: w.queue }])
    minReplicas: 0
    maxReplicas: 5
  }
}]

module aks 'modules/aks.bicep' = if (!useAca) {
  scope: rg
  name: 'aks'
  params: { location: location, tags: tags, resourceToken: resourceToken, logAnalyticsWorkspaceId: monitoring.outputs.workspaceId, tenantId: subscription().tenantId }
}

var fnIdentity = first(filter(idList, x => x.workload == 'bpm-functions'))
var publicBase = useAca ? caEnv!.outputs.defaultDomain : ''

module functions 'modules/functions.bicep' = {
  scope: rg
  name: 'functions'
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    identityId: fnIdentity.id
    identityClientId: fnIdentity.clientId
    identityPrincipalId: fnIdentity.principalId
    appInsightsConnectionString: monitoring.outputs.connectionString
    appSettings: [
      { name: 'AIIP_WORKLOAD_REGISTRATION', value: 'bpm-invoice-orchestrator' }
      { name: 'AZURE_TENANT_ID', value: empty(entraTenantId) ? subscription().tenantId : entraTenantId }
      { name: 'AIIP_TOOL_GATEWAY_URL', value: 'https://tool-gateway.${publicBase}' }
      { name: 'AIIP_A2A_GATEWAY_URL', value: 'https://a2a-gateway.${publicBase}' }
      { name: 'AIIP_IDENTITY_URL', value: 'https://identity-gateway.internal.${publicBase}' }
    ]
  }
}

module apim 'modules/apim.bicep' = {
  scope: rg
  name: 'apim'
  params: {
    location: location
    tags: tags
    resourceToken: resourceToken
    sku: apimSku
    publisherEmail: apimPublisherEmail
    entraTenantId: entraTenantId
    backends: {
      tools: 'https://tool-gateway.${publicBase}'
      mcp: 'https://mcp-gateway.${publicBase}'
      a2a: 'https://a2a-gateway.${publicBase}'
      events: 'https://event-gateway.${publicBase}'
      bpm: 'https://${functions.outputs.hostname}/api'
    }
    audiences: {
      tools: 'api://aiip-tool-gateway'
      mcp: 'api://aiip-mcp-gateway'
      a2a: 'api://aiip-care-planner'
      events: 'api://aiip-event-gateway'
      bpm: 'api://aiip-bpm-invoice-orchestrator'
    }
  }
}

module frontDoor 'modules/frontdoor.bicep' = if (deployFrontDoor) {
  scope: rg
  name: 'frontdoor'
  params: { tags: tags, resourceToken: resourceToken, originHostName: apim.outputs.hostname }
}

output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.outputs.loginServer
output AZURE_KEY_VAULT_NAME string = kv.outputs.name
output APIM_GATEWAY_URL string = apim.outputs.gatewayUrl
output FRONT_DOOR_HOST string = deployFrontDoor ? frontDoor!.outputs.endpointHostName : ''
output SERVICE_BUS_NAMESPACE string = bus.outputs.fqdn
output EVENT_GRID_TOPIC_ENDPOINT string = events.outputs.endpoint
output FUNCTION_APP_NAME string = functions.outputs.name
output APPLICATIONINSIGHTS_CONNECTION_STRING string = monitoring.outputs.connectionString
output WORKLOAD_IDENTITIES array = map(idList, i => { workload: i.workload, clientId: i.clientId, principalId: i.principalId })
