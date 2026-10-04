# Implementation guide

How the integration platform is built, in the order you would build it again. Each step names the files, the proof command and the tests that keep it working. Everything runs offline (`AIIP_MODE=local`, the default) against the sandbox stand-ins.

## 1. Set up

```bash
python -m venv .venv && . .venv/bin/activate     # Python 3.13
pip install -e ".[dev]"
ruff check . && pytest -q
```

## 2. Shared controls first

Build `src/aiip/shared/` before any gateway: JWT validation with tenant allow-list, `kv://` secret references, the token bucket, TTL cache and circuit breaker, approvals bound to arguments, untrusted-text screening, sanitized errors, `integration_span` and the signed, hash-chained audit log. See [components/shared-pipeline.md](components/shared-pipeline.md). Proof: `pytest tests/test_27_integration_plane.py`.

## 3. Identity: as whom?

Declare one app registration per workload in `identity/registrations.py`, then the Identity Gateway with OBO, agent and hybrid exchanges and a local issuer that follows Entra's rules ([components/identity-gateway.md](components/identity-gateway.md)). Proof: `pytest tests/test_33_identity.py`.

## 4. Systems of record behind connectors

Write a stand-in for each vendor in `fakesaas/` with fault injection, then the connector pack that maps auth, idempotency, errors and canonical models ([components/sandbox-stand-ins.md](components/sandbox-stand-ins.md), [components/connectors.md](components/connectors.md)). Proof: `pytest tests/test_34_saas_connectors.py`.

## 5. The gateways

1. Tool Gateway: registry and invocation pipeline ([components/tool-gateway.md](components/tool-gateway.md)).
2. MCP Gateway and the three servers ([components/mcp-gateway.md](components/mcp-gateway.md)).
3. A2A Gateway and directory ([components/a2a-gateway.md](components/a2a-gateway.md)).
4. Event Gateway, bus and workers ([components/event-gateway.md](components/event-gateway.md)).

Proof: `pytest tests/test_28_tool_gateway.py tests/test_29_mcp_gateway.py tests/test_30_a2a_gateway.py tests/test_31_events.py`.

## 6. Agents and the money process

Thin MAF agents that call only gateways ([components/agents.md](components/agents.md)), then the Durable Functions vendor-invoice orchestration with its 48-hour approval timer and compensation, and the Logic Apps alternative ([components/bpm-vendor-invoice.md](components/bpm-vendor-invoice.md)). Proof: `pytest tests/test_32_bpm.py`.

## 7. Runtime safety

Policy prover, sandbox, kill switch and the out-of-band monitor ([components/runtime-safety.md](components/runtime-safety.md)). Proof: `python scripts/run_safety_evals.py --no-write`.

## 8. Gates and contracts

Golden cases, the eval and contract gate, and generated contracts in `control-plane/` ([components/evals-and-contracts.md](components/evals-and-contracts.md)). Proof: `python scripts/run_eval_gate.py --no-write` and `python scripts/export_contracts.py --check`.

## 9. Observability

`integration_span` attributes, completion metrics and the KQL file that generates both dashboards ([components/observability.md](components/observability.md)). Proof: `python scripts/build_dashboards.py --check`.

## 10. Infrastructure and pipelines

Bicep and Terraform, validated in CI, deploy gated off ([components/infrastructure.md](components/infrastructure.md), [deployment.md](deployment.md)). Nothing has been deployed.

## 11. Keep the docs honest

`scripts/doc_drift.py` regenerates every `<!-- output: ... -->` and `<!-- code: ... -->` block in the Markdown files; `scripts/doc_demo.py` runs the demo and gates with timings and ids masked. CI runs `python scripts/doc_drift.py --check`, so pasted output and code excerpts must match the code.

```bash
python scripts/doc_drift.py          # refresh after a code change
python scripts/doc_drift.py --check  # what CI runs
```

## 12. End to end

`python scripts/demo.py` starts the gateways, agents, servers, workers and stand-ins as separate local processes and runs every drill over real HTTP; `--inproc` runs the same scenarios in one process.
