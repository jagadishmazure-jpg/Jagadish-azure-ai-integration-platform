# Cost estimate

No prices are quoted here on purpose: they vary by region, currency, agreement and date. This
page lists **what you are billed for** and the official pricing pages. Use the
[Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/) with these dimensions.
Nothing in this repository has been deployed.

## Default (cost-minimized) profile

| Resource | Default SKU / setting | Billing dimensions | Pricing |
|---|---|---|---|
| Container Apps (gateways, agents, MCP servers, workers) | Consumption profile, `minReplicas=0` | vCPU-seconds and GiB-seconds while active, requests; monthly free grant | [container-apps](https://azure.microsoft.com/pricing/details/container-apps/) |
| API Management | Consumption | calls per million; free monthly grant | [api-management](https://azure.microsoft.com/pricing/details/api-management/) |
| Azure Functions (Durable, vendor invoice) | Flex Consumption | executions, GB-seconds (on-demand), optional always-ready instances | [functions](https://azure.microsoft.com/pricing/details/functions/) |
| Storage account (Functions + Durable task hub) | Standard LRS | capacity, transactions (the task hub polls queues and tables) | [storage](https://azure.microsoft.com/pricing/details/storage/) |
| Service Bus | Basic | operations per million (queues only; topics need Standard) | [service-bus](https://azure.microsoft.com/pricing/details/service-bus/) |
| Event Grid (custom topic) | Basic | operations per million; monthly free grant | [event-grid](https://azure.microsoft.com/pricing/details/event-grid/) |
| Key Vault | Standard | operations per 10,000 | [key-vault](https://azure.microsoft.com/pricing/details/key-vault/) |
| Container Registry | Basic | per day per registry, storage over included amount | [container-registry](https://azure.microsoft.com/pricing/details/container-registry/) |
| Log Analytics + Application Insights | Pay-as-you-go, **1 GB/day cap**, 30-day retention | GB ingested, retention beyond included days | [monitor](https://azure.microsoft.com/pricing/details/monitor/) |
| Managed identities | user-assigned, one per workload | no charge | - |
| Model calls (only with `AIIP_MODE=azure`) | Foundry deployment of a small model | input/output tokens | [ai-foundry](https://azure.microsoft.com/pricing/details/ai-foundry/) |

The offline demo and CI use none of these.

## Opt-in profiles (off by default)

| Switch | Adds | Billing dimensions | Pricing |
|---|---|---|---|
| `AIIP_DEPLOY_FRONT_DOOR=true` | Front Door Standard | base fee per profile, requests, data transfer | [frontdoor](https://azure.microsoft.com/pricing/details/frontdoor/) |
| `AIIP_PRIVATE_NETWORKING=true` | VNet, private endpoints, private DNS zones; **forces Service Bus Premium** | per endpoint-hour + GB processed; Premium messaging units per hour; DNS zones and queries | [private-link](https://azure.microsoft.com/pricing/details/private-link/), [service-bus](https://azure.microsoft.com/pricing/details/service-bus/), [dns](https://azure.microsoft.com/pricing/details/dns/) |
| `AIIP_COMPUTE_PROFILE=aks` | AKS (free tier control plane) + node pool VMs | VM hours for nodes, disks, load balancer | [kubernetes-service](https://azure.microsoft.com/pricing/details/kubernetes-service/), [virtual-machines](https://azure.microsoft.com/pricing/details/virtual-machines/linux/) |
| `AIIP_APIM_SKU=BasicV2/StandardV2` | dedicated APIM (needed for VNet integration, higher limits) | units per hour | [api-management](https://azure.microsoft.com/pricing/details/api-management/) |
| `AIIP_SERVICEBUS_SKU=Standard` | topics/subscriptions, sessions | base per hour + operations | [service-bus](https://azure.microsoft.com/pricing/details/service-bus/) |

## Levers

* Keep `minReplicas=0`; workers scale from queue depth with KEDA, so idle means no compute.
* Keep the Log Analytics daily cap; sample traces in the OTel SDK before raising it.
* The budget per event class in the Event Gateway is also a **cost control**: an event storm
  parks events instead of starting model runs.
* Tear down with `azd down --purge` (Key Vault and APIM are soft-deleted otherwise).
