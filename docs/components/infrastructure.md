# Infrastructure (`infra/`, `azure.yaml`, workflows)

Bicep for `azd` and a Terraform twin with cost-minimized defaults and opt-in Front Door, Private Link and AKS. Validated in CI; not deployed.

**Sections:** [1. Purpose](#1-purpose) · [2. Architecture](#2-architecture) · [3. How it works](#3-how-it-works) · [4. Key files](#4-key-files) · [5. Code excerpts](#5-code-excerpts) · [6. Configuration](#6-configuration) · [7. Commands](#7-commands) · [8. Real output](#8-real-output) · [9. Tests and eval gates](#9-tests-and-eval-gates) · [10. Guardrails](#10-guardrails) · [11. Security and governance](#11-security-and-governance) · [12. Observability](#12-observability) · [13. Failure modes](#13-failure-modes) · [14. Mapping to Azure services](#14-mapping-to-azure-services) · [15. Limitations](#15-limitations) · [16. Interview talking points](#16-interview-talking-points) · [17. Adopt this](#17-adopt-this)

## 1. Purpose

Declare every Azure service the gateways expect, keyless and reviewable, and prove it compiles without a subscription.

## 2. Architecture

```mermaid
flowchart TB
    M[main.bicep] --> MON[monitoring] & ID[identity] & KV[keyvault] & ACR[registry]
    M --> CAE[containerapps-env] --> CA[containerapp per service]
    M --> SB[servicebus] & EGR[eventgrid] & FN[functions]
    M --> APIM[apim]
    M -. opt-in .-> FD[frontdoor] & NET[network] & AKS[aks]
```

## 3. How it works

1. `azure.yaml` defines one image with many commands, one per service.
2. `main.bicep` composes the modules at subscription scope with cost-minimized defaults.
3. `infra/terraform` mirrors it with plan tests.
4. CI builds Bicep with warnings as errors; the infra workflow runs Terraform checks, tflint and checkov; deploy is gated.

## 4. Key files

| File | What it does |
|---|---|
| `infra/main.bicep` | entry point |
| `infra/modules/` | modules |
| `infra/terraform/` | Terraform twin |
| `.github/workflows/` | CI, infra, deploy, teardown |
| `tests/test_36_reference_architecture.py` | static checks |

## 5. Code excerpts

Cost-minimized defaults and opt-in flags in `main.bicep`:

<!-- code: infra/main.bicep:15-41 -->
```bicep
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
```
<!-- /code -->

## 6. Configuration

`infra/main.parameters.json` maps azd environment values; opt-in flags enable Front Door, Private Link and AKS.

## 7. Commands

```bash
bicep build infra/main.bicep --stdout > /dev/null
pytest tests/test_36_reference_architecture.py -q
```

## 8. Real output

<!-- output: ls infra/modules -->
```text
README.md
aks.bicep
apim.bicep
containerapp.bicep
containerapps-env.bicep
eventgrid.bicep
frontdoor.bicep
functions.bicep
identity.bicep
keyvault.bicep
monitoring.bicep
network.bicep
registry.bicep
servicebus.bicep
```
<!-- /output -->

<!-- output: python -m pytest --co -q -p no:cacheprovider tests/test_36_reference_architecture.py | grep '::' -->
```text
tests/test_36_reference_architecture.py::test_bicep_builds_without_errors_or_warnings
tests/test_36_reference_architecture.py::test_cost_minimized_defaults
tests/test_36_reference_architecture.py::test_no_keys_or_connection_strings_for_data_plane
tests/test_36_reference_architecture.py::test_every_workload_has_its_own_identity_and_azd_service
tests/test_36_reference_architecture.py::test_workload_registrations_exist_in_code
tests/test_36_reference_architecture.py::test_workload_commands_point_at_real_modules
tests/test_36_reference_architecture.py::test_parameters_file_maps_azd_env
tests/test_36_reference_architecture.py::test_deploy_workflow_is_gated_oidc_and_promotes_with_approval
tests/test_36_reference_architecture.py::test_ci_runs_tests_lint_gate_and_bicep
tests/test_36_reference_architecture.py::test_cost_estimate_has_no_invented_prices
tests/test_36_reference_architecture.py::test_dockerfile_uses_non_root_user
```
<!-- /output -->

## 9. Tests and eval gates

Static architecture tests above (one skips where the Bicep CLI is missing) plus Bicep build and Terraform checks in CI.

## 10. Guardrails

- Cheapest SKUs by default.
- Deploy workflow off until a subscription exists.

## 11. Security and governance

- Managed identity and Key Vault; OIDC for pipelines.

## 12. Observability

Monitoring module provisions Log Analytics and Application Insights.

## 13. Failure modes

| Failure | Result |
|---|---|
| Bicep warning | CI fails |
| Terraform invalid | infra workflow fails |

## 14. Mapping to Azure services

Container Apps, API Management, Service Bus, Event Grid, Functions, Key Vault, Container Registry, Log Analytics, Application Insights, optional Front Door, Private Link and AKS.

## 15. Limitations

- Never deployed.

## 16. Interview talking points

- Same design in two IaC tools, both validated offline.

## 17. Adopt this

1. Copy `infra/`, edit parameters, run `azd up` in a sandbox subscription.
