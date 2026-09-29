# `modules/functions`

Durable Functions host on Flex Consumption (FC1, Python 3.13) with identity-based storage (shared keys disabled) and the three storage data roles it needs.

| File | What it does |
|---|---|
| [`main.tf`](main.tf) | Resources (and module calls for a root stack). |
| [`outputs.tf`](outputs.tf) | Values exported to the caller / the pipeline. |
| [`variables.tf`](variables.tf) | Inputs with types, defaults and validation rules. |
| [`versions.tf`](versions.tf) | Terraform and provider version constraints. |
