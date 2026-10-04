# Adopt this

How another team can reuse the integration platform: what to take, how to configure it and how to extend it without weakening the controls. Each page in [components/](components/README.md) ends with its own "Adopt this" steps.

## What you can take

| If you need | Take | Start from |
|---|---|---|
| one policy chokepoint for agent tool calls | Tool Gateway + registry | [tool-gateway.md](components/tool-gateway.md) |
| governed MCP servers | MCP Gateway, catalog, server middleware | [mcp-gateway.md](components/mcp-gateway.md) |
| agent-to-agent delegation with contracts | A2A Gateway, specs, cards | [a2a-gateway.md](components/a2a-gateway.md) |
| user-scoped agents (OBO) and workload identities | Identity Gateway and registrations | [identity-gateway.md](components/identity-gateway.md) |
| event-driven agents that survive storms | Event Gateway, canonical events, worker | [event-gateway.md](components/event-gateway.md) |
| a money process with human approval | Durable orchestration or Logic Apps workflow | [bpm-vendor-invoice.md](components/bpm-vendor-invoice.md) |
| a vendor integration | connector pack + stand-in | [connectors.md](components/connectors.md) |
| containment for misbehaving agents | runtime safety and monitor | [runtime-safety.md](components/runtime-safety.md) |

## Configure

1. Copy the repository or the packages you need; all settings are in `.env.example` and none are secret.
2. Edit `identity/registrations.py` for your workloads and OBO targets, then run `python scripts/export_contracts.py`.
3. Declare your tools in `tools/registry.py` (side-effect class, identity policy, business key, HITL threshold, schemas) and add them to agent cards in `a2a/specs.py`.
4. Point connector packs at your vendor sandboxes using `kv://` secret references; never put a raw secret in configuration.
5. Tune rate limits and breakers with `AIIP_TENANT_RPS`, `AIIP_TENANT_BURST`, `AIIP_BREAKER_FAILURES` and `AIIP_BREAKER_RESET_S`.
6. For Azure, edit `infra/main.parameters.json` and run `azd up` in a sandbox subscription.

## Extend

- **New vendor:** stand-in in `fakesaas/`, pack in `connectors/`, registry entry, contract tests.
- **New tool:** `ToolDef`, card update, contract export, a failure drill in `tests/test_28_tool_gateway.py`.
- **New event type:** class in `events/canonical.py` with business key and budget, graph in `events/graphs.py`.
- **New safety rule:** rule in `safety/policy.yaml`, attack and benign scenarios in `evals/safety-scenarios.yaml`.
- **New docs output:** add an `<!-- output: cmd -->` or `<!-- code: path::name -->` block and run `python scripts/doc_drift.py`.

## Keep these invariants

- Agents hold no connections; every call goes through a gateway.
- Commits above threshold need an approval bound to the exact arguments.
- Every commit has an idempotency key.
- HTTP 200 with a business error is a failure.
- Server and vendor output is untrusted and screened.
- CI stays green on lint, tests, eval, safety, contract, dashboard and doc-drift gates, the HTTP demo and the Bicep build.

## Ownership

`.github/CODEOWNERS` assigns the repository to @jagadishmazure-jpg. An adopting team should replace it with owners per folder, for example the identity team for `src/aiip/identity/` and the platform team for `control-plane/`.
