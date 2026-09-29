# infra

Bicep for `azd`, plus a Terraform twin in [`terraform/`](terraform/README.md). `bicep build infra/main.bicep` compiles clean with no warnings (CI enforces it). Nothing has been deployed. See [docs/deploy.md](../docs/deploy.md) and [docs/cost-estimate.md](../docs/cost-estimate.md).

| File | What it does |
|---|---|
| [`main.bicep`](main.bicep) | Subscription-scope entry point wiring every module; cost-minimized defaults |
| [`main.parameters.json`](main.parameters.json) | azd environment variable → parameter mapping |
| [`modules/`](modules/) | One module per resource family |
| [`terraform/`](terraform/README.md) | The same infrastructure in Terraform, with CAF names, dev/prod tfvars, a partial remote-state backend and offline plan tests; its README explains when to pick which tool |
