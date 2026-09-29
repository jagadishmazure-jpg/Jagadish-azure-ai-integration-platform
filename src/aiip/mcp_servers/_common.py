"""Shared plumbing for the MCP servers: workload principal, bearer-token middleware for HTTP hosting."""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from aiip.config import TENANTS, stable_id
from aiip.connectors.base import ConnectorContext, ConnectorError
from aiip.identity.registrations import MCP_SERVERS
from aiip.shared import errors as E
from aiip.shared.auth import Principal, validate_token


def workload(server: str, subject: str = "") -> ConnectorContext:
    """Connector context for the server's own managed identity (MCP servers are agent-scoped)."""
    p = Principal(
        tenant_id=TENANTS["contoso"],
        actor_app_id=stable_id(f"app:mcp-{server}"),
        actor=f"mcp-{server}",
        subject_id="",
        subject=subject or f"mcp-{server}",
        identity_mode="agent",
        audience="",
    )
    return ConnectorContext(principal=p, timeout_s=4.0)


def fail(exc: ConnectorError) -> dict:
    """Tool-level error as data: code + short message, no stack trace."""
    return {"error": exc.error_class, "message": exc.message[:160]}


class BearerAuth(BaseHTTPMiddleware):
    """Only the MCP Gateway (app role McpServer.Invoke on api://aiip-mcp-servers) may call a server."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/healthz":
            return JSONResponse({"ok": True})
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error": "authn_failed"}, 401)
        try:
            p = await validate_token(auth.split(" ", 1)[1], MCP_SERVERS)
        except E.GatewayError:
            return JSONResponse({"error": "authn_failed"}, 401)
        if "McpServer.Invoke" not in p.roles:
            return JSONResponse({"error": "authz_deny"}, 403)
        return await call_next(request)
