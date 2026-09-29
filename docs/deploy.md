# Deploy (azd)

> **Not deployed.** No Azure resources were created while building this repository. CI only runs
> `bicep build`. This page is the intended path.

## Prerequisites

* Azure CLI, [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/)
  (`azd`) and Bicep. Images build remotely in ACR (`remoteBuild: true`), so local Docker is optional.
* Subscription role: **Owner**, or **Contributor + Role Based Access Control Administrator**
  (the template creates role assignments for managed identities).
* For `AIIP_MODE=azure` with a hosted model: an existing Foundry project endpoint and a model
  deployment (default name `gpt-5-mini`). The template does not create the Foundry project.

## 1. Entra app registrations (tenant objects, not in Bicep)

`control-plane/app-registrations.json` lists one registration per workload with its app ID URI,
app roles it **exposes**, app roles it is **granted** on other APIs and allowed OBO targets. For
each entry:

```bash
az ad app create --display-name aiip-<workload> --identifier-uris api://aiip-<workload>
# expose app roles / a user_impersonation scope as listed; grant roles to the callers listed
az ad app federated-credential create --id <appId> --parameters '{
  "name": "mi", "issuer": "https://login.microsoftonline.com/<tenant>/v2.0",
  "subject": "<principal id of the workload user-assigned managed identity>",
  "audiences": ["api://AzureADTokenExchange"] }'
```

The federated credential makes the workload's managed identity the only way to authenticate as
that registration: no client secrets. Record the client IDs as `AIIP_APPID_<WORKLOAD>` in the azd
environment (the managed identities are created by Bicep, so run `azd provision` once, then add
the credentials).

## 2. Provision and deploy

```bash
azd auth login
azd env new aiip-dev --location eastus2
azd env set AIIP_ENTRA_TENANT_ID <tenant-id>
azd env set AIIP_APIM_PUBLISHER_EMAIL platform-team@example.com
azd up
```

`azd up` provisions `infra/main.bicep` (subscription scope, creates `rg-<env>`), runs the
`prepackage` hook that copies `src/aiip` next to `functions/function_app.py`, builds the single
platform image in ACR and rolls out every Container App (matched by `azd-service-name` tags) plus
the Functions app.

Seed vendor credentials into Key Vault using the secret names referenced by the connector packs
(`kv://aiip-local-kv/<name>` resolves to the deployed vault through `AIIP_KEYVAULT_NAME`), and point
each pack at a vendor **sandbox** with `AIIP_SAAS_<VENDOR>_URL` before any production tenant.

## Parameters

| azd env var | Default | Effect |
|---|---|---|
| `AIIP_APIM_SKU` | `Consumption` | `Developer`, `BasicV2`, `StandardV2` |
| `AIIP_SERVICEBUS_SKU` | `Basic` | `Standard`, `Premium` (forced to Premium with private networking) |
| `AIIP_LOG_DAILY_QUOTA_GB` | `1` | Log Analytics ingestion cap, `-1` = none |
| `AIIP_DEPLOY_FRONT_DOOR` | `false` | Front Door Standard in front of APIM |
| `AIIP_PRIVATE_NETWORKING` | `false` | VNet, internal Container Apps environment, private endpoints for Key Vault and Service Bus |
| `AIIP_COMPUTE_PROFILE` | `containerapps` | `aks` provisions an AKS cluster (workload identity, KEDA) instead; workloads are then applied with the same image |

## CI/CD with GitHub Actions and OIDC (disabled by default)

The pipeline is described in [`deployment.md`](deployment.md): pull-request checks for the
Terraform stack, a `deploy.yml` workflow that goes dev -> prod through GitHub Environments with
required reviewers, a `deploy_tool` input (`bicep` or `terraform`), OIDC login with federated
credentials (no secrets), smoke tests and a manual `teardown.yml`. Every deploy job is gated
behind the repository variable `DEPLOY_ENABLED`, which is not set.

## Tear down

```bash
azd down --purge
```
