"""MCP server catalog. Integration engineers own server quality; this catalog is how the fleet is
discovered and governed. Servers start read-only (`writes_enabled=False`) until reviewed."""

from __future__ import annotations

import importlib
from dataclasses import dataclass

from aiip.shared.http import service_url


@dataclass(frozen=True)
class ServerEntry:
    name: str
    description: str
    owner: str
    system: str
    module: str
    agent_role: str  # app role an agent-scoped caller needs on the MCP gateway
    write_role: str = ""  # app role needed to *request* a write
    writes_enabled: bool = False
    approver_role: str = "OpsApprover"
    standin: bool = True

    @property
    def service(self) -> str:
        return f"mcp-{self.name}"

    def url(self) -> str | None:
        base = service_url(self.service)
        return f"{base}/mcp" if base else None

    def inproc(self):
        return importlib.import_module(self.module).server

    def public(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "owner": self.owner,
            "system": self.system,
            "transport": "streamable-http" if self.url() else "in-process",
            "mode": "read-write (HITL)" if self.writes_enabled else "read-only",
            "agent_role": self.agent_role,
            "standin": self.standin,
        }


OWNER = "Jagadish Meduri (integration engineering)"
SERVERS: dict[str, ServerEntry] = {
    s.name: s
    for s in (
        ServerEntry(
            "sap-orders",
            "SAP sales orders and deliveries (OData)",
            OWNER,
            "sap",
            "aiip.mcp_servers.sap_orders",
            "Orders.Read",
        ),
        ServerEntry(
            "servicenow-incidents",
            "ServiceNow incident table",
            OWNER,
            "servicenow",
            "aiip.mcp_servers.servicenow_incidents",
            "Incidents.Read",
            write_role="Incidents.Write",
            writes_enabled=True,
        ),
        ServerEntry(
            "sql-warehouse",
            "Databricks-style SQL warehouse, allow-listed tables",
            OWNER,
            "databricks",
            "aiip.mcp_servers.sql_warehouse",
            "Analytics.Read",
        ),
    )
}
