# aiip package

The platform code. Every subpackage is one part of the integration plane; `shared/` is what all five gateways have in common.

| File | What it does |
|---|---|
| [`__init__.py`](__init__.py) | Package marker and version |
| [`config.py`](config.py) | Runtime settings (`AIIP_MODE`, tenants, demo people directory) |
| [`topology.py`](topology.py) | Launches the whole local topology (15 processes) over real HTTP |
| [`demo.py`](demo.py) | End-to-end demo scenarios (HTTP topology or `--inproc`) |
| [`identity/`](identity/) | Identity Gateway: token exchange (OBO / client credentials), app registrations, local issuer |
| [`tools/`](tools/) | Tool Gateway and tool registry |
| [`mcp/`](mcp/) | MCP Gateway and server catalog |
| [`mcp_servers/`](mcp_servers/) | Three MCP servers over enterprise APIs (SAP orders, ServiceNow incidents, SQL warehouse) |
| [`a2a/`](a2a/) | A2A Gateway, agent directory, agent specs and cards |
| [`agents/`](agents/) | Microsoft Agent Framework domain agents and the customer-care planner |
| [`events/`](events/) | Event Gateway, canonical events, bus abstraction, workers and event graphs |
| [`bpm/`](bpm/) | Durable vendor-invoice orchestration, activities, local replay runtime |
| [`connectors/`](connectors/) | SaaS connector packs (Salesforce, ServiceNow, Workday, Dataverse, SAP OData, Jira) |
| [`fakesaas/`](fakesaas/) | Sandbox stand-ins for the vendor APIs (not the real vendors) |
| [`safety/`](safety/) | Runtime safety layer: sandbox, policy prover, supervisor, out-of-band monitor, kill switch |
| [`shared/`](shared/) | Auth, secrets, telemetry, audit, resilience, errors, schema, untrusted-content screening |
