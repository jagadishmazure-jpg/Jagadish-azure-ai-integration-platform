"""Shared fixtures. Everything runs in-process and offline: services talk through httpx ASGI
transports (no sockets), the identity gateway signs tokens with a local key, and vendors are the
sandbox stand-ins under aiip.fakesaas."""

from __future__ import annotations

import logging
import os

import pytest

os.environ.setdefault("AIIP_MODE", "local")
os.environ.setdefault("AIIP_LOCAL_VAULT_SEED", "test-seed-not-a-secret")
for _name in [k for k in os.environ if k.startswith("AIIP_") and k.endswith("_URL")]:
    os.environ.pop(_name)  # never leak a running topology into unit tests

logging.getLogger("a2a.server.events.event_queue_v2").setLevel(logging.ERROR)


@pytest.fixture(autouse=True)
def reset_all():
    from aiip.a2a import gateway as a2a_gw
    from aiip.connectors import registry
    from aiip.events import gateway as ev_gw
    from aiip.fakesaas import state
    from aiip.mcp import gateway as mcp_gw
    from aiip.shared import telemetry
    from aiip.tools import gateway as tool_gw

    state.reset()
    registry.reset()
    tool_gw.reset_state()
    mcp_gw.reset_state()
    ev_gw.reset_state()
    a2a_gw.AUDIT.reset()
    telemetry.LEDGER.reset()
    telemetry.clear_spans()
    yield
    state.FAULTS.clear()


# ----------------------------------------------------------------------------- helpers
async def user_token(username: str, audience: str) -> str:
    from aiip.identity.client import login

    return await login(username, audience)


async def agent_token(app: str, target: str, tenant: str = "contoso") -> str:
    from aiip.identity.client import IdentityClient

    return await IdentityClient(app).agent_token(target, tenant)


async def obo_token(username: str, via_agent: str, target: str) -> str:
    """User signs in to `via_agent`; that agent exchanges the token on-behalf-of for `target`."""
    from aiip.identity.client import IdentityClient
    from aiip.identity.registrations import agent_uri

    tok = await user_token(username, agent_uri(via_agent))
    return await IdentityClient(via_agent).obo(tok, target)


async def call(
    service: str, method: str, path: str, token: str | None = None, headers: dict | None = None, **kw
):
    from aiip.shared import http

    h = dict(headers or {})
    if token:
        h["authorization"] = f"Bearer {token}"
    async with http.client(service, timeout=30) as c:
        r = await c.request(method, path, headers=h, **kw)
    return r.status_code, (
        r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
    )


async def tool(
    token: str,
    name: str,
    args: dict,
    key: str | None = None,
    approval: str | None = None,
    traceparent: str | None = None,
):
    h = {}
    if key:
        h["idempotency-key"] = key
    if approval:
        h["x-approval-id"] = approval
    if traceparent:
        h["traceparent"] = traceparent
    return await call("tool-gateway", "POST", f"/v1/tools/{name}/invoke", token, h, json={"args": args})


async def mcp(
    token: str, server: str, name: str, arguments: dict, key: str | None = None, approval: str | None = None
):
    h = {}
    if key:
        h["idempotency-key"] = key
    if approval:
        h["x-approval-id"] = approval
    return await call(
        "mcp-gateway",
        "POST",
        f"/v1/servers/{server}/tools/{name}/call",
        token,
        h,
        json={"arguments": arguments},
    )


async def fault(vendor: str, mode: str, count: int = 50, delay_s: float = 0.0):
    return await call(
        "fake-saas",
        "POST",
        f"/_admin/faults/{vendor}",
        json={"mode": mode, "count": count, "delay_s": delay_s},
    )


def fast_timeout(monkeypatch, tool_name: str, seconds: float = 0.3) -> None:
    """Shrink one tool's timeout so timeout drills stay fast."""
    import dataclasses

    from aiip.tools.registry import TOOLS

    monkeypatch.setitem(TOOLS, tool_name, dataclasses.replace(TOOLS[tool_name], timeout_s=seconds))
