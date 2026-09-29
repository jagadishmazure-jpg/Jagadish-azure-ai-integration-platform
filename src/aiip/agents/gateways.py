"""How agents reach systems: only through the Tool Gateway and MCP Gateway, with a token for the
right principal. Agents never see a connection string, vendor token or secret."""

from __future__ import annotations

from typing import Any

from aiip.shared import errors as E
from aiip.shared import http


def _raise(r) -> None:
    err = r.json().get("error", {})
    raise E.GatewayError(
        err.get("code", E.INTERNAL), err.get("message", "gateway error"), detail=err.get("detail")
    )


async def call_tool(
    name: str,
    args: dict[str, Any],
    *,
    token: str,
    traceparent: str | None = None,
    idempotency_key: str | None = None,
    approval_id: str | None = None,
) -> dict[str, Any]:
    headers = {"authorization": f"Bearer {token}"}
    if traceparent:
        headers["traceparent"] = traceparent
    if idempotency_key:
        headers["idempotency-key"] = idempotency_key
    if approval_id:
        headers["x-approval-id"] = approval_id
    async with http.client("tool-gateway", timeout=15) as c:
        r = await c.post(f"/v1/tools/{name}/invoke", json={"args": args}, headers=headers)
    if r.status_code != 200:
        _raise(r)
    return r.json()


async def call_mcp(
    server: str,
    tool: str,
    arguments: dict[str, Any],
    *,
    token: str,
    traceparent: str | None = None,
    idempotency_key: str | None = None,
    approval_id: str | None = None,
) -> dict[str, Any]:
    headers = {"authorization": f"Bearer {token}"}
    if traceparent:
        headers["traceparent"] = traceparent
    if idempotency_key:
        headers["idempotency-key"] = idempotency_key
    if approval_id:
        headers["x-approval-id"] = approval_id
    async with http.client("mcp-gateway", timeout=15) as c:
        r = await c.post(
            f"/v1/servers/{server}/tools/{tool}/call", json={"arguments": arguments}, headers=headers
        )
    if r.status_code != 200:
        _raise(r)
    return r.json()
