# infra/modules

| File | What it does |
|---|---|
| [`monitoring.bicep`](monitoring.bicep) | Log Analytics (daily cap, 30-day retention) + Application Insights |
| [`identity.bicep`](identity.bicep) | One user-assigned managed identity per workload |
| [`keyvault.bicep`](keyvault.bicep) | Key Vault (RBAC) + Secrets User for gateways that resolve vendor credentials |
| [`registry.bicep`](registry.bicep) | ACR Basic, admin user off, AcrPull for workloads |
| [`servicebus.bicep`](servicebus.bicep) | Namespace, queues with maxDeliveryCount + dead-lettering, sender/receiver roles |
| [`eventgrid.bicep`](eventgrid.bicep) | Custom topic (CloudEvents, managed identity) + subscriptions delivering to queues |
| [`containerapps-env.bicep`](containerapps-env.bicep) | Container Apps environment (Consumption profile) |
| [`containerapp.bicep`](containerapp.bicep) | One workload: HTTP app or KEDA queue-scaled worker |
| [`functions.bicep`](functions.bicep) | Flex Consumption Functions app + storage with identity-based roles |
| [`apim.bicep`](apim.bicep) | APIM (Consumption) APIs for the gateways with JWT validation and rate-limit policies |
| [`frontdoor.bicep`](frontdoor.bicep) | Optional Front Door Standard |
| [`network.bicep`](network.bicep) | Optional VNet, private endpoints and DNS zones |
| [`aks.bicep`](aks.bicep) | Optional AKS profile (free tier, workload identity, KEDA) |
