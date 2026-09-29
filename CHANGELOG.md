# Changelog

Notable changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). There are no versioned releases, so entries are grouped by date.

## Unreleased

### Added

- `docs/best-practices.md`: cloud and agentic AI practices with honest status and links.
- Architecture decision records in `docs/adr/`.
- `SECURITY.md`, `CONTRIBUTING.md` and this changelog.

### Changed

- README sections follow one order: what, why, architecture, run, test, deploy, limits.

## 2026-09-29

### Added

- Terraform twin of the Bicep in `infra/terraform` (per-workload identities, Service Bus, Event Grid, Container Apps with KEDA workers, Flex Functions, APIM, opt-in Front Door, private networking and hardened AKS; offline `terraform test`).
- GitHub Actions `infra.yml`, `deploy.yml` (dev -> prod, Bicep or Terraform, OIDC, gated by `DEPLOY_ENABLED`) and `teardown.yml`; `docs/deployment.md`.
- Runtime safety layer: sandbox, policy prover, signed audit chains, out-of-band monitor and safety gate.
- Initial platform: identity, tool, MCP, A2A and event gateways; SaaS connector packs and stand-ins; vendor-invoice BPM on Durable Functions and Logic Apps; contracts, eval gate, dashboards; Bicep; offline tests; CI; docs.

### Changed

- The earlier `azd` deploy workflow (gated by `ENABLE_DEPLOY`) was replaced by the new pipeline.
