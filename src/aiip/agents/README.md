# agents

Domain agents built with Microsoft Agent Framework. They hold no connections: every tool is a call to the Tool or MCP Gateway with a token from the Identity Gateway.

| File | What it does |
|---|---|
| [`maf.py`](maf.py) | MAF plumbing: deterministic offline chat client (real `BaseChatClient` + function invocation) and `FoundryChatClient` in Azure mode |
| [`domain.py`](domain.py) | CRM, ERP, data and AP-invoice agent skills |
| [`planner.py`](planner.py) | Customer-care planner: user-scoped fan-out over A2A, hybrid KPI lookup, idempotent case creation |
| [`gateways.py`](gateways.py) | The only way agents reach systems: Tool / MCP gateway calls with the right token |
| [`apps.py`](apps.py) | ASGI app per agent |
| [`__main__.py`](__main__.py) | `python -m aiip.agents <agent-id>` |
| [`__init__.py`](__init__.py) | Package marker |
