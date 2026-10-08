# `infra/terraform/tests`

Offline plan tests. `terraform test` plans against a mocked `azurerm` provider (no credentials, nothing created) and checks CAF names, tags, the per-workload identity count, and the containerapps / AKS / private-networking shapes.

| File | What it does |
|---|---|
| [`plan.tftest.hcl`](plan.tftest.hcl) | Offline `terraform test`: plans with mocked providers and asserts naming, tags, alert rules, diagnostic settings, Defender off by default and the per-profile shape. No Azure credentials needed. |
