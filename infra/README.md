# infra

Bicep for `azd`. `bicep build infra/main.bicep` compiles clean with no warnings (CI enforces it). Nothing has been deployed. See [docs/deploy.md](../docs/deploy.md) and [docs/cost-estimate.md](../docs/cost-estimate.md).

| File | What it does |
|---|---|
| [`main.bicep`](main.bicep) | Subscription-scope entry point wiring every module; cost-minimized defaults |
| [`main.parameters.json`](main.parameters.json) | azd environment variable → parameter mapping |
| [`modules/`](modules/) | One module per resource family |
