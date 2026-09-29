# Interview guide

How to walk through this repository in 5, 15 or 45 minutes, and the questions it is built to
answer.

## The one-liner

> Agents in this platform never hold a system connection. They call five gateways (Tool, MCP,
> A2A, Event, Identity) that decide who the call is made as, whether it is allowed, and whether it
> is safe to repeat, and every hop is traced down to whether the business step completed.

## 5 minutes: run it

```bash
python scripts/demo.py          # 15 processes over HTTP, ~5 s
```

Point at three lines of output: OBO fan-out as alice, bob's `authz_deny` on the same question,
and "retry after gateway restart: replay_source=vendor; SAP holds 1 invoice".

## 15 minutes: the questions hiring managers ask

| Question | Where the answer lives |
|---|---|
| OBO vs app-only: when do you use which? | [identity.md](identity.md); `aiip/identity/broker.py`; tool `identity` policy in `aiip/tools/registry.py` |
| How do you make an SAP write safe to retry? | gateway idempotency cache + SAP `Repeatability-Request-ID`; demo step 3; `tests/test_28_tool_gateway.py` |
| SAP returned 200 but it failed. What does your telemetry say? | `business_reject` result class; demo step 4; [observability.md](observability.md) |
| Event Grid to a worker: what about duplicates, storms, poison? | [events.md](events.md); demo step 8; `tests/test_31_events.py` |
| Where does human approval live, and can an agent forge it? | [bpm.md](bpm.md); `tests/test_32_bpm.py::test_forged_approval_event_cannot_post_money` |
| What stops agent A from calling agent B's SAP skills? | A2A caller allow-list + hop cap; `aiip/a2a/gateway.py`; `tests/test_30_a2a_gateway.py` |
| MCP servers return text a model will read. How do you trust it? | untrusted-content screening; demo step 7; `aiip/shared/untrusted.py` |
| One SaaS OAuth flow, end to end? | Salesforce JWT bearer and Dataverse OBO in `aiip/connectors/auth.py` |
| What does the agent card contain? | `control-plane/agent-cards/*.json` (owner, version, SLA, input schema, eval score, side-effect class) |
| What does this cost idle? | [cost-estimate.md](cost-estimate.md): scale-to-zero defaults, billing dimensions |

## 45 minutes: design trade-offs to discuss

* **Gateways as separate services vs a library.** Separate services cost a network hop but give
  one review, one breaker per system, one rate limit per tenant. A library would be copied into
  every agent with its own bugs.
* **HITL placement.** The approval check sits in the Tool Gateway, bound to business key and
  amount, so neither the orchestrator nor an agent can skip it. The BPM owns *waiting*; the
  gateway owns *permission*.
* **Idempotency at two layers.** The gateway's cache handles fast retries; the vendor's native
  mechanism handles retries after the gateway lost its memory. The demo proves the second layer
  by restarting the cache.
* **Budget per event class.** Parking, not dropping, keeps the business event while protecting
  the model budget.
* **Durable Functions vs Logic Apps.** Durable gives code-level tests and replay; Logic Apps gives
  operations teams a designer and built-in connectors. Both definitions are here.
* **What I would do next in a real tenant.** Real Entra registrations with federated credentials,
  one vendor sandbox (ServiceNow PDI or Dataverse trial) wired through `AIIP_SAAS_*_URL`, APIM
  policies exercised end to end, and KQL validated on live App Insights data.

## Industry mapping

| Industry | Same platform, different events and tools |
|---|---|
| Manufacturing / supply chain | late shipment events → customer notification; order simulation before commit |
| Financial services | invoice / payment processes on BPM rails with amount-bound approvals and audit chains |
| Healthcare | OBO so record-level access still applies; minimal-field mapping as a data boundary |
| Retail | order and returns events with dedupe on business key; storm budgets on promotions |
| IT operations | ServiceNow incidents from events; injected text in tickets screened before a model sees it |
| Public sector | per-workload identities, hash-chained audit, private networking profile |
