# `modules/apim`

API Management in front of the services: optional Entra ID JWT validation, spoofable-header stripping, rate limit, traceparent injection (and, in the agent platform, a kill-switch named value).

| File | What it does |
|---|---|
| [`main.tf`](main.tf) | Resources (and module calls for a root stack). |
| [`outputs.tf`](outputs.tf) | Values exported to the caller / the pipeline. |
| [`variables.tf`](variables.tf) | Inputs with types, defaults and validation rules. |
| [`versions.tf`](versions.tf) | Terraform and provider version constraints. |
