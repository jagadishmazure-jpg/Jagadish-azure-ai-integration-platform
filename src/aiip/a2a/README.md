# a2a

Agent-to-agent integration: a governed JSON-RPC proxy plus a directory of versioned agent cards.

| File | What it does |
|---|---|
| [`gateway.py`](gateway.py) | A2A Gateway (FastAPI): directory endpoints and the governed proxy (audience, caller allow-list, hop cap, tenant, schema) |
| [`directory.py`](directory.py) | Version resolution, caller allow-lists, hop cap |
| [`specs.py`](specs.py) | Agent contracts: skills, input schemas, side-effect class, SLA, owner, allowed tools and callers |
| [`cards.py`](cards.py) | AgentSpec → A2A 1.0 AgentCard (a2a-sdk protobuf) with integration metadata |
| [`server.py`](server.py) | A2A server factory used by every agent; re-validates the bearer token per task |
| [`client.py`](client.py) | A2A client that always goes through the gateway, propagating traceparent, tenant and hops |
| [`__init__.py`](__init__.py) | Package marker |
