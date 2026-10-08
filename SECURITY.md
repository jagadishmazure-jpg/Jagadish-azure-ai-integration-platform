# Security policy

## Supported versions

Only the `main` branch is maintained. There are no released versions.

## Reporting a vulnerability

Please do not open a public issue with the details.

1. Use GitHub private vulnerability reporting: **Security** tab -> **Report a vulnerability** on [Jagadish-azure-ai-integration-platform](https://github.com/jagadishmazure-jpg/Jagadish-azure-ai-integration-platform/security).
2. If that button is not shown, open an issue titled `Security contact request` with no technical details, and I will reply with a private channel.

I aim to acknowledge a report within 5 working days. This is a personal portfolio maintained by one person, so there is no formal SLA or bug bounty.

## Scope

This repository is a demonstration. It runs offline against mocks and synthetic data and has never been deployed to a live Azure tenant. The SaaS systems are sandbox stand-ins. In scope: anything in the code, infrastructure definitions or workflows that would be unsafe if someone deployed it as written (for example, a role that is broader than documented, a secret that could leak through CI, or a guardrail that can be bypassed). Out of scope: the deterministic mocks and sandbox stand-ins themselves.

## What the repo already does

- No secrets in the repo; a hygiene test fails CI on secret-like values ([`tests/test_repo_hygiene.py`](tests/test_repo_hygiene.py)).
- CI signs in to Azure with OIDC only ([ADR 0003](docs/adr/0003-oidc-and-managed-identity.md)).
- OBO so agents act with the user's permissions; one managed identity per workload with resource-scoped roles.
- Default-deny policy, sandboxed tool code, signed audit chains and an out-of-band monitor with a kill switch ([`src/aiip/safety/`](src/aiip/safety/README.md)).
- checkov scans the Terraform on every change, with each skipped check justified in [`.checkov.yaml`](.checkov.yaml).
- **Supply chain:** every third-party GitHub Action is pinned to a full commit SHA with its version in a comment, and every workflow starts from read-only `permissions`. Dependabot proposes weekly, grouped updates ([`.github/dependabot.yml`](.github/dependabot.yml)); CodeQL scans the Python code and the workflow files ([`codeql.yml`](.github/workflows/codeql.yml)); gitleaks scans the full git history in CI. A test (`test_workflows_are_hardened`) fails if an action is left unpinned or a workflow loses its `permissions` block.
- **Known scanner false positives** are listed by fingerprint in [`.gitleaksignore`](.gitleaksignore), each with the reason (for example a public Azure built-in role ID); none is a credential.
- **GitHub settings:** secret scanning with push protection, Dependabot alerts and security updates, private vulnerability reporting, and a ruleset on `main` that blocks force-pushes and branch deletion and requires the CI checks before a pull request can merge. The maintainer (repository admin) can still push directly to `main`, so for direct pushes the checks run after the push rather than before it.
- Full status of each control: [`docs/best-practices.md`](docs/best-practices.md).
