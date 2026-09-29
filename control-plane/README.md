# control-plane

Generated contracts, checked in so reviewers can read them without running code. Regenerate with `python scripts/export_contracts.py`; CI fails if they drift.

| File | What it does |
|---|---|
| [`agent-cards/`](agent-cards/) | A2A 1.0 agent cards per agent version (owner, SLA, input schema, eval score, side-effect class) |
| [`tool-registry.json`](tool-registry.json) | Every tool with side-effect class, identity policy, HITL rule and schemas |
| [`mcp-catalog.json`](mcp-catalog.json) | MCP servers with owner, mode and allowed agents |
| [`app-registrations.json`](app-registrations.json) | Entra app registrations: one per workload, roles and OBO targets |
