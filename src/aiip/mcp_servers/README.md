# mcp_servers

Three MCP servers (official `mcp` SDK, streamable HTTP) wrapping enterprise APIs. They accept only tokens for `api://aiip-mcp-servers` with the `McpServer.Invoke` role, which only the MCP Gateway holds.

| File | What it does |
|---|---|
| [`sap_orders.py`](sap_orders.py) | Read-only SAP sales orders / deliveries (reuses the SAP connector pack) |
| [`servicenow_incidents.py`](servicenow_incidents.py) | ServiceNow incidents: reads plus a HITL-gated create |
| [`sql_warehouse.py`](sql_warehouse.py) | Databricks-style SQL: SELECT-only, table allow-list, PII tables not exposed |
| [`_common.py`](_common.py) | Workload principal and bearer-token middleware |
| [`__main__.py`](__main__.py) | `python -m aiip.mcp_servers <server>` |
| [`__init__.py`](__init__.py) | Package marker |
