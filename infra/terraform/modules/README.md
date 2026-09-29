# `infra/terraform/modules`

Reusable modules called by the root stack. Each mirrors a module in [`../../modules/`](../../modules/README.md) (the Bicep), keeps its defaults, and takes `resource_group_name`, `location` and `tags` as inputs.

| File | What it does |
|---|---|
| [`aks/`](aks/README.md) | Optional AKS profile: Free tier, autoscaling system pool, OIDC issuer + workload identity, Azure RBAC, KEDA, Container Insights, Azure CNI overlay with network policy. |
| [`apim/`](apim/README.md) | API Management in front of the services: optional Entra ID JWT validation, spoofable-header stripping, rate limit, traceparent injection (and, in the agent platform, a kill-switch named value). |
| [`containerapp/`](containerapp/README.md) | One Container App (HTTP service or KEDA queue worker) on a user-assigned identity, pulling from ACR with that identity. The image is ignored after creation because the pipeline rolls images. |
| [`containerapps-env/`](containerapps-env/README.md) | Container Apps managed environment on the Consumption workload profile, logging to Log Analytics; optional VNet integration. |
| [`eventgrid/`](eventgrid/README.md) | Event Grid topic (CloudEvents 1.0, keys disabled) with one subscription per event type routed to a Service Bus queue through the topic's managed identity. |
| [`frontdoor/`](frontdoor/README.md) | Optional Azure Front Door Standard profile, endpoint, origin group, origin (APIM) and HTTPS-only route. |
| [`functions/`](functions/README.md) | Durable Functions host on Flex Consumption (FC1, Python 3.13) with identity-based storage (shared keys disabled) and the three storage data roles it needs. |
| [`identity/`](identity/README.md) | User-assigned managed identities, one per key in a static map, so role assignments can be keyed before the principal ids exist. |
| [`keyvault/`](keyvault/README.md) | Key Vault in RBAC mode (no access policies), soft delete 7 days, purge protection as a variable, and `Key Vault Secrets User` for the given workload identities. |
| [`monitoring/`](monitoring/README.md) | Log Analytics workspace (PerGB2018, optional daily cap) and a workspace-based Application Insights component. |
| [`naming/`](naming/README.md) | CAF naming helper: `<type>-<workload>-<env>-<region>-<instance>` (for example `rg-agentplat-dev-eus2-001`), compressed forms for Key Vault (24 chars, region dropped), ACR and Storage (alphanumeric), and an optional suffix for globally unique names. No resources. |
| [`network/`](network/README.md) | Optional private networking: VNet, delegated Container Apps subnet, private-endpoint subnet, NSG, private DNS zones and VNet links. |
| [`private-endpoint/`](private-endpoint/README.md) | One private endpoint plus its private DNS zone group. |
| [`registry/`](registry/README.md) | Azure Container Registry with the admin user disabled; `AcrPull` for the workload identities. |
| [`servicebus/`](servicebus/README.md) | Service Bus namespace (SAS disabled, TLS 1.2), queues with dead-lettering, and sender / receiver roles. |
