# `.github/workflows/`: CI and the (disabled) deploy pipeline

| File | What it does |
|---|---|
| [`ci.yml`](ci.yml) | On push/PR: ruff lint + format check, the offline pytest suite, the eval + contract gate, the runtime-safety gate (every attack scenario contained, zero false quarantines), stale-contract and stale-dashboard checks, the full end-to-end demo over real HTTP (15 local processes), and a separate job that builds `infra/main.bicep` with a pinned Bicep CLI and fails on any warning. |
| [`deploy.yml`](deploy.yml) | Manual `azd up` / `azd down` over GitHub OIDC. The job is skipped unless the repository variable `ENABLE_DEPLOY` is `true`; the federated credential subject is `repo:jagadishmazure-jpg/Jagadish-azure-ai-integration-platform:environment:dev`. No client secret is stored anywhere. |
