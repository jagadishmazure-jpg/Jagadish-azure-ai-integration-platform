# `.github/workflows/`: CI, infrastructure checks, and the gated deploy pipeline

`deploy.yml` and `teardown.yml` jobs are skipped unless the repository variable `DEPLOY_ENABLED`
is `true`. It is not set and nothing has been deployed. Walkthrough: [`docs/deployment.md`](../../docs/deployment.md).

| File | What it does |
|---|---|
| [`ci.yml`](ci.yml) | On push/PR: ruff lint + format check, the offline pytest suite, the eval + contract gate, the runtime-safety gate (every attack scenario contained, zero false quarantines), stale-contract and stale-dashboard checks, the doc-drift check (`scripts/doc_drift.py --check`: pasted output and code excerpts in the Markdown match the code), the full end-to-end demo over real HTTP (15 local processes), and a separate job that builds `infra/main.bicep` with a pinned Bicep CLI and fails on any warning. A `secrets` job runs gitleaks over the full git history. |
| [`codeql.yml`](codeql.yml) | CodeQL for Python and for the workflow files (`actions`), on push, pull request and weekly. Results appear under Security -> Code scanning and do not fail the build. |
| [`infra.yml`](infra.yml) | On push/PR: `terraform fmt -check`, `init -backend=false`, `validate` and `terraform test` (mocked provider) for `infra/terraform`; tflint; checkov with [`.checkov.yaml`](../../.checkov.yaml); builds the platform image and smoke-runs `/healthz`. `terraform plan` runs only when the Azure OIDC variables exist, otherwise it passes with a notice. |
| [`deploy.yml`](deploy.yml) | Push to `main` or manual (`deploy_tool`: `terraform` / `bicep`): `preflight` reports the gate, then `build` -> `deploy-dev` (environment `dev`) -> `deploy-prod` (environment `prod`, required reviewers). OIDC login via `azure/login` (no client secret), image promotion with `az acr import`, Functions zip deploy, smoke tests. |
| [`teardown.yml`](teardown.yml) | Manual only: destroys one environment with the tool that created it, after typing the environment name again. Same gate; prod needs approval. |

**Supply chain.** Every third-party action is pinned to a full commit SHA with the version in a comment, and every workflow starts from `permissions: contents: read`; jobs that need more (OIDC sign-in, CodeQL uploads) ask for it themselves. Dependabot ([`../dependabot.yml`](../dependabot.yml)) proposes weekly grouped updates that move the SHA and the comment together, and `tests/test_36_reference_architecture.py::test_workflows_are_hardened` fails CI if an action is left unpinned.
