# docs

Design notes for each part of the platform. Start with `architecture.md`.

| File | What it does |
|---|---|
| [architecture.md](architecture.md) | Reference topology, the five gateways, hosting, local vs Azure mode |
| [identity.md](identity.md) | "As whom?": OBO, agent-scoped and hybrid identity, rules enforced in code |
| [events.md](events.md) | Canonical events, admission (dedupe, budget), worker settlement, dead-lettering |
| [bpm.md](bpm.md) | Durable vendor-invoice process: agents as activities, HITL, timeout, compensation |
| [connectors.md](connectors.md) | SaaS connector packs: auth, mapping, idempotency, error taxonomy, rate limits |
| [observability.md](observability.md) | Span attributes, result classes, metrics, dashboards, audit |
| [interview-guide.md](interview-guide.md) | How to walk through the repo; question → evidence map; industry mapping |
| [sdk-notes.md](sdk-notes.md) | Pinned SDK versions, surprises, verified vs unverified |
| [cost-estimate.md](cost-estimate.md) | Billing dimensions per resource with official pricing links |
| [deploy.md](deploy.md) | azd path, Entra registrations, parameters, OIDC workflow |
| [deployment.md](deployment.md) | GitHub Actions pipeline: diagram, PR checks, dev -> prod approval gates, Bicep or Terraform, OIDC federated-credential setup, smoke tests, teardown, FDE notes |
