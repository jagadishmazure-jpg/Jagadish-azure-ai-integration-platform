# mcp

MCP Gateway: fleet manager for MCP servers.

| File | What it does |
|---|---|
| [`gateway.py`](gateway.py) | Catalog, discovery, governed invocation, HITL approvals for writes, output screening, audit, metrics |
| [`catalog.py`](catalog.py) | Server catalog: owner, system, mode (read-only / read-write HITL), transport, allowed agents |
| [`__init__.py`](__init__.py) | Package marker |
