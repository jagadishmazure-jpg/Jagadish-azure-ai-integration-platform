# `modules/aks`

Optional AKS profile: Free tier, autoscaling system pool, OIDC issuer + workload identity, Azure RBAC, KEDA, Container Insights, Azure CNI overlay with network policy.

| File | What it does |
|---|---|
| [`main.tf`](main.tf) | Resources (and module calls for a root stack). |
| [`outputs.tf`](outputs.tf) | Values exported to the caller / the pipeline. |
| [`variables.tf`](variables.tf) | Inputs with types, defaults and validation rules. |
| [`versions.tf`](versions.tf) | Terraform and provider version constraints. |
