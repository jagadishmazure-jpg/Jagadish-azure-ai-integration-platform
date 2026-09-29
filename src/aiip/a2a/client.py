"""A2A client. Calls go *through the A2A gateway* (never straight to a peer) as JSON-RPC 1.0
`SendMessage` with traceparent, tenant and hop headers."""

from __future__ import annotations

import uuid
from typing import Any

from aiip.shared import errors as E
from aiip.shared import http
from aiip.shared.tracecontext import child_traceparent, new_traceparent


async def send(
    callee: str,
    skill: str,
    payload: dict[str, Any],
    *,
    token: str,
    tenant_id: str,
    traceparent: str | None = None,
    version: str | None = None,
    hops: int = 0,
    timeout_s: float = 20.0,
) -> dict[str, Any]:
    headers = {
        "authorization": f"Bearer {token}",
        "A2A-Version": "1.0",
        "traceparent": child_traceparent(traceparent) if traceparent else new_traceparent(),
        "x-tenant-id": tenant_id,
        "x-a2a-hops": str(hops),
    }
    if version:
        headers["x-agent-version"] = version
    body = {
        "jsonrpc": "2.0",
        "id": uuid.uuid4().hex,
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": uuid.uuid4().hex,
                "role": "ROLE_USER",
                "parts": [{"data": {"skill": skill, "input": payload}}],
            }
        },
    }
    async with http.client("a2a-gateway", timeout=timeout_s) as c:
        r = await c.post(f"/v1/agents/{callee}/a2a", json=body, headers=headers)
    if r.status_code != 200:
        err = (
            r.json().get("error", {})
            if r.headers.get("content-type", "").startswith("application/json")
            else {}
        )
        raise E.GatewayError(
            err.get("code", E.UNAVAILABLE), err.get("message", f"a2a gateway HTTP {r.status_code}")
        )
    data = r.json()
    out = data.get("artifact")
    if not isinstance(out, dict):
        raise E.GatewayError(E.INTERNAL, "a2a response without artifact")
    if "error" in out:
        raise E.GatewayError(
            out["error"] if out["error"] in E.HTTP_STATUS else E.INTERNAL, str(out.get("detail", ""))[:200]
        )
    return {**out, "screening": data.get("screening", {}), "resolved_version": data.get("resolved_version")}
