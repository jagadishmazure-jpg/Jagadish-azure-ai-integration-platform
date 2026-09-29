# SDK notes

All versions below were resolved together on Python 3.13 and are pinned in `pyproject.toml`
(and `functions/requirements.txt` for the Functions app). Nothing here was deployed to Azure.

| Package | Version | Used for |
|---|---|---|
| agent-framework-core | 1.19.0 | `Agent`, `BaseChatClient`, function invocation for all domain agents |
| agent-framework-foundry | 1.13.1 | `FoundryChatClient` in `AIIP_MODE=azure` |
| azure-ai-projects | 2.6.1 | pulled by the Foundry client (capped `<2.7` by agent-framework-foundry) |
| a2a-sdk[fastapi] | 1.1.5 | A2A 1.0 agent cards, JSON-RPC server, client |
| mcp | 2.2.0 | MCP servers (streamable HTTP) and the gateway's client sessions |
| fastapi / uvicorn | 0.141.1 / 0.54.0 | the five gateways, BPM host, fake SaaS |
| httpx | 0.28.1 | all service-to-service HTTP (real or in-process ASGI) |
| pydantic / jsonschema | 2.13.5 / 4.26.0 | canonical models, tool and event schemas |
| pyjwt[crypto] / cryptography | 2.15.0 / 50.0.1 | RS256 token validation and the local issuer |
| msal | 1.39.0 | OBO and client credentials in `MsalBroker` |
| azure-identity | 1.25.3 | managed identity / `DefaultAzureCredential` |
| azure-keyvault-secrets | 4.11.2 | `kv://` resolution in Azure mode |
| azure-servicebus / azure-eventgrid | 7.14.3 / 4.22.1 | Azure bus code paths |
| azure-functions / azure-functions-durable | 2.3.0 / 1.7.0 | Functions v2 programming model + Durable orchestration |
| opentelemetry-api / -sdk | 1.44.0 | spans and metrics |
| azure-monitor-opentelemetry | 1.8.10 | exporter to Application Insights |
| Bicep CLI | 0.47.16 | `bicep build` in CI (warnings fail the job) |

## Surprises worth knowing

* **MAF custom chat client needs a private mixin.** To get tool calling on a custom
  `BaseChatClient` (the deterministic offline client), the class must also inherit
  `FunctionInvocationLayer`, which is only importable from `agent_framework._tools` in 1.19. A
  minor release could move it; the import is isolated in `aiip/agents/maf.py`.
* **Version caps chain.** `agent-framework-foundry 1.13.1` requires `azure-ai-projects<2.7`, and
  `azure-monitor-opentelemetry 1.8.10` caps `opentelemetry-sdk<1.45`. Upgrading OTel alone breaks
  the resolver.
* **azure-functions 2.x requires Python ≥ 3.13.** That pins the Functions app runtime and the
  container base image to 3.13.
* **Durable SDK history JSON.** Replaying an orchestrator through
  `DurableOrchestrationContext.from_json` with hand-built history needs a `Version` key on every
  history event (`HistoryEvent` reads it unconditionally in 1.7.0).
* **MCP streamable HTTP needs its lifespan.** `mcp` 2.2's session manager starts in the ASGI
  lifespan; an in-process test client that skips lifespan gets a `RuntimeError` on the first
  request. Tests enter `app.router.lifespan_context(app)`.
* **httpx `ASGITransport` re-raises app exceptions by default.** For in-process tests to behave
  like a real server (500 with a sanitized body), the transport is built with
  `raise_app_exceptions=False`.
* **a2a-sdk 1.1.5 AgentCard is protobuf.** Cards are built as protobuf messages and serialized
  with `MessageToDict`; the SDK also logs a harmless warning when an agent replies with a single
  message and the queue closes, which is silenced at ERROR level.
* **Bicep lambdas.** Building an object literal inside a `map()` lambda over a filtered array did
  not compile (BCP errors); `main.bicep` builds a lookup object first and indexes into it.

## What is verified vs not

| Verified offline | Not verified against real services |
|---|---|
| All five gateways, MCP servers and agents over real HTTP (15 processes, `scripts/demo.py`) | Entra ID: real app registrations, OBO consent, federated credentials |
| Token validation logic with RS256 JWKS (local issuer) | `MsalBroker` against a live tenant |
| Orchestrator against the Durable SDK replay context | Durable Functions on a real Flex Consumption host / task hub |
| `function_app.py` indexes 11 functions with the v2 model | Logic Apps workflow (`workflow.json`) has not been deployed |
| Bus semantics with the in-memory peek-lock stand-in | `ServiceBusClientBus` / `EventGridPublisher` against real namespaces |
| Connector packs against sandbox stand-ins | Real Salesforce, ServiceNow, Workday, Dataverse, SAP, Jira tenants |
| `bicep build` clean, no warnings | `azd provision` / `azd deploy`; APIM policies at runtime |
| Dashboards generated from KQL | KQL against real Application Insights tables |
