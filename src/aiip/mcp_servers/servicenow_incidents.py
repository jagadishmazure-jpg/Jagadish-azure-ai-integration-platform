"""MCP server over the ServiceNow incident table (sandbox stand-in). Incident descriptions are
free text typed by end users: the gateway treats them as untrusted."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from aiip.connectors.base import ConnectorError
from aiip.connectors.servicenow import ServiceNowPack
from aiip.mcp_servers._common import fail, workload

server = MCPServer("servicenow-incidents", instructions="ServiceNow incidents (sandbox stand-in).")
_pack = ServiceNowPack()
READ = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)


class IncidentView(BaseModel):
    incident_id: str = ""
    number: str = ""
    short_description: str = ""
    description: str = ""
    state: str = ""
    priority: str = ""
    error: str = ""
    message: str = ""


class IncidentList(BaseModel):
    incidents: list[IncidentView] = []
    error: str = ""
    message: str = ""


@server.tool(annotations=READ)
async def list_incidents(category: str = "integration", limit: int = 10) -> IncidentList:
    """Open incidents in a category."""
    try:
        d = (
            await _pack.invoke(
                "list_incidents",
                {"category": category, "limit": min(limit, 25)},
                workload("servicenow-incidents"),
            )
        ).data
    except ConnectorError as exc:
        return IncidentList(**fail(exc))
    return IncidentList(
        incidents=[
            IncidentView(**{k: v for k, v in i.items() if k in IncidentView.model_fields})
            for i in d["incidents"]
        ]
    )


@server.tool(annotations=READ)
async def get_incident(incident_id: str) -> IncidentView:
    """One incident including its free-text description."""
    try:
        d = (
            await _pack.invoke("get_incident", {"incident_id": incident_id}, workload("servicenow-incidents"))
        ).data
    except ConnectorError as exc:
        return IncidentView(**fail(exc))
    return IncidentView(**{k: v for k, v in d.items() if k in IncidentView.model_fields})


@server.tool(annotations=WRITE)
async def create_incident(
    short_description: str, business_key: str, idempotency_key: str, priority: int = 4
) -> IncidentView:
    """Open an incident; idempotent on idempotency_key (correlation_id)."""
    ctx = workload("servicenow-incidents")
    ctx.idempotency_key = idempotency_key
    try:
        d = (
            await _pack.invoke(
                "create_incident",
                {"short_description": short_description, "priority": priority, "business_key": business_key},
                ctx,
            )
        ).data
    except ConnectorError as exc:
        return IncidentView(**fail(exc))
    return IncidentView(**{k: v for k, v in d.items() if k in IncidentView.model_fields})
