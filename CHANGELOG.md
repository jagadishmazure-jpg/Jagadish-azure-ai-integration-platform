# Changelog

Notable changes, newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). There are no versioned releases, so entries are grouped by milestone, newest first.

## Unreleased

### Added

- Opt-in Azure AI Content Safety Prompt Shields after the regex screen in `shared/untrusted.py` (`src/aiip/shared/content_safety.py`, ported from azure-agent-platform): with `AIIP_PROMPT_SHIELDS=1` and `AZURE_CONTENT_SAFETY_ENDPOINT`, MCP output, A2A artifacts and supervisor-retrieved text are also screened, keyless and fail closed. Offline the regex screen remains the only screen. 5 tests with a fake transport; not run against Azure.
- Threat model (`docs/security/threat-model.md`): STRIDE, OWASP Top 10 for LLM Applications and MITRE ATLAS mapped to this repository's components, each row with its control, test evidence and built / planned status.
- SBOM job in CI: an SPDX JSON software bill of materials of the source tree on every run (artifact `sbom.spdx.json`).
- Container supply chain: base images pinned by digest, pip/uv removed from runtime images, a Trivy image scan that fails on fixable HIGH/CRITICAL findings, an image SBOM, and keyless build provenance for the image archive on `main` (`actions/attest-build-provenance`; verification steps in `SECURITY.md`).
- Supply-chain hardening: every GitHub Action pinned to a commit SHA with a version comment, top-level `permissions` on every workflow, a gitleaks job in CI, a CodeQL workflow, `.github/dependabot.yml` and a guard test (`test_workflows_are_hardened`).
- GitHub settings: Dependabot alerts and security updates, private vulnerability reporting and a `main` ruleset (no force-push or deletion; CI required on pull requests).
- Component docs in `docs/components/` (17 standard sections each), `docs/implementation-guide.md`, `docs/adopt-this.md`, and the `scripts/doc_drift.py` CI check that keeps pasted output and code excerpts in sync with the code (`scripts/doc_demo.py` masks timings and ids).
- `CODEOWNERS`.
- `docs/best-practices.md`: cloud and agentic AI practices with honest status and links.
- Architecture decision records in `docs/adr/`.
- `SECURITY.md`, `CONTRIBUTING.md` and this changelog.

### Changed

- The README test count now matches the collected suite, and the README demo excerpt is generated from a real run.
- The demo prints the event-storm outcome counts in sorted order, so its output is stable.
- README sections follow one order: what, why, architecture, run, test, deploy, limits.

## Milestone 1: platform, runtime safety and delivery pipeline

### Added

- Terraform twin of the Bicep in `infra/terraform` (per-workload identities, Service Bus, Event Grid, Container Apps with KEDA workers, Flex Functions, APIM, opt-in Front Door, private networking and hardened AKS; offline `terraform test`).
- GitHub Actions `infra.yml`, `deploy.yml` (dev -> prod, Bicep or Terraform, OIDC, gated by `DEPLOY_ENABLED`) and `teardown.yml`; `docs/deployment.md`.
- Runtime safety layer: sandbox, policy prover, signed audit chains, out-of-band monitor and safety gate.
- Initial platform: identity, tool, MCP, A2A and event gateways; SaaS connector packs and stand-ins; vendor-invoice BPM on Durable Functions and Logic Apps; contracts, eval gate, dashboards; Bicep; offline tests; CI; docs.

### Changed

- The earlier `azd` deploy workflow (gated by `ENABLE_DEPLOY`) was replaced by the new pipeline.
