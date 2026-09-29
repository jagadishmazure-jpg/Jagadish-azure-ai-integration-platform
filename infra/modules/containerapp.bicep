// One workload of the integration plane: a gateway, MCP server, A2A agent or event worker. All use
// the same image with a different command. HTTP workloads scale on concurrency; workers have no
// ingress and scale on Service Bus queue length via KEDA, authenticating with their own identity.
param location string
param tags object
param name string
param environmentId string
param identityId string
param identityClientId string
param registryServer string
param command array
@allowed(['http', 'worker'])
param kind string = 'http'
param external bool = false
param targetPort int = 8080
param env array = []
param minReplicas int = 0
param maxReplicas int = 3
param queueName string = ''
param serviceBusNamespace string = ''
param healthPath string = '/healthz'
param cpu string = '0.5'
param memory string = '1Gi'

var httpScale = [{ name: 'http', http: { metadata: { concurrentRequests: '20' } } }]
var queueScale = [
  {
    name: 'queue-length'
    custom: {
      type: 'azure-servicebus'
      identity: identityId
      metadata: { namespace: serviceBusNamespace, queueName: queueName, messageCount: '5' }
    }
  }
]

resource app 'Microsoft.App/containerApps@2025-01-01' = {
  name: name
  location: location
  tags: union(tags, { 'azd-service-name': name })
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityId}': {} } }
  properties: {
    environmentId: environmentId
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: kind == 'worker' ? null : {
        external: external
        targetPort: targetPort
        transport: 'auto'
        allowInsecure: !external
      }
      registries: [{ server: registryServer, identity: identityId }]
    }
    template: {
      containers: [
        {
          name: 'main'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest' // azd swaps in the platform image
          command: command
          env: concat([
            { name: 'PORT', value: string(targetPort) }
            { name: 'HOST', value: '0.0.0.0' }
            { name: 'AZURE_CLIENT_ID', value: identityClientId }
          ], env)
          resources: { cpu: json(cpu), memory: memory }
          probes: kind == 'worker' || empty(healthPath) ? [] : [
            { type: 'Liveness', httpGet: { path: healthPath, port: targetPort }, periodSeconds: 30 }
          ]
        }
      ]
      scale: { minReplicas: minReplicas, maxReplicas: maxReplicas, rules: kind == 'worker' ? queueScale : httpScale }
    }
  }
}

output name string = app.name
output fqdn string = kind == 'worker' ? '' : app.properties.configuration.ingress.fqdn
