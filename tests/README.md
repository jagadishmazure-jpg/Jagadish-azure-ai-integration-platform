# tests

Offline, fast (~10 s). Files are numbered by the integration topic they cover; each includes failure drills.

| File | What it does |
|---|---|
| [`conftest.py`](conftest.py) | In-process topology: every service mounted as ASGI, tokens from the local issuer, fault injection, state reset |
| [`test_27_integration_plane.py`](test_27_integration_plane.py) | Five separate gateways, shared auth, KV references only, no direct system access from agents, full demo |
| [`test_28_tool_gateway.py`](test_28_tool_gateway.py) | Allow-lists, side-effect classes, rate limit, cache, breaker, schemas, idempotency, timeouts, sanitized errors, audit |
| [`test_29_mcp_gateway.py`](test_29_mcp_gateway.py) | Catalog, discovery, read-only default, HITL writes, SQL guardrails, injection screening, server auth |
| [`test_30_a2a_gateway.py`](test_30_a2a_gateway.py) | Cards, caller allow-list, hop cap, trace/tenant propagation, versioning, schema |
| [`test_31_events.py`](test_31_events.py) | Admission, dedupe, budget, poison and dead-letter, completion events, agent identity |
| [`test_32_bpm.py`](test_32_bpm.py) | Orchestration paths, HITL, timeout, compensation, forged approvals, Durable SDK replay, Functions indexing |
| [`test_33_identity.py`](test_33_identity.py) | OBO vs client credentials vs hybrid, OBO targets, app roles, actor/subject, ACL enforcement |
| [`test_34_saas_connectors.py`](test_34_saas_connectors.py) | Per-pack contract tests: mapping, idempotent writes, error taxonomy, rate-limit hints, minimal fields |
| [`test_35_observability.py`](test_35_observability.py) | Span attributes, result classes, 200-with-error as failure, completion metrics, dashboards |
| [`test_36_reference_architecture.py`](test_36_reference_architecture.py) | Bicep compiles, cost-minimized defaults, azd services, workflows, cost doc rules |
| [`test_37_runtime_safety.py`](test_37_runtime_safety.py) | Sandbox isolation and limits, ACA sessions adapter, policy prover default deny + proofs, supervisor, signed telemetry, gateway kill switch |
| [`test_38_out_of_band_monitor.py`](test_38_out_of_band_monitor.py) | Monitor detectors, quarantine + measured containment latency, off-path and cross-process monitor, safety eval gate |
| [`test_repo_hygiene.py`](test_repo_hygiene.py) | No personal emails or secrets, READMEs everywhere, required docs, stand-ins labeled |
| [`test_repo_docs.py`](test_repo_docs.py) | Component docs have the 17 sections in order with a mermaid diagram, are indexed, guides exist, CODEOWNERS, no placeholders or prose dates, README test count equals the collected count |
