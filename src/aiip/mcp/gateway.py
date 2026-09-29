"""MCP Gateway (FastAPI).

GET  /v1/servers                          catalog (owner, system, mode, transport)
GET  /v1/servers/{server}/tools           discovery: schemas + annotations + governance verdict
POST /v1/servers/{server}/tools/{tool}/call   governed invocation
POST /v1/approvals, /v1/approvals/{id}/decision   HITL for writes
GET  /v1/audit, /v1/metrics

Governance: agent-card allow-list per server, app role per server, read-only by default, writes
need catalog enablement + HITL approval + idempotency key, arguments validated against the tool's
advertised input schema, output validated against its output schema and screened as untrusted."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from fastapi import Depends, FastAPI, Header
from mcp import Client
from pydantic import BaseModel

from aiip.a2a.specs import allowed_mcp_servers
from aiip.identity.client import IdentityClient
from aiip.identity.registrations import MCP_GW, MCP_SERVERS
from aiip.mcp.catalog import SERVERS, ServerEntry
from aiip.shared import errors as E
from aiip.shared import telemetry
from aiip.shared.approvals import ApprovalStore
from aiip.shared.audit import AuditLog, digest
from aiip.shared.auth import Principal, principal_dependency
from aiip.shared.resilience import CircuitBreaker, CircuitOpen, TokenBucket
from aiip.shared.schema import errors as schema_errors
from aiip.shared.tracecontext import trace_id
from aiip.shared.untrusted import screen

app = FastAPI(title="MCP Gateway", version="1.0.0")
E.install_error_handlers(app)
auth = principal_dependency(MCP_GW)
SERVICE = "mcp-gateway"
AUDIT = AuditLog("mcp-gateway")
APPROVALS = ApprovalStore()
RATE = TokenBucket(capacity=40, refill_per_s=20)
BREAKERS: dict[str, CircuitBreaker] = {}
IDEMPOTENCY: dict[str, dict[str, Any]] = {}
_DISCOVERY: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_identity = IdentityClient("mcp-gateway")
CALL_TIMEOUT_S = 6.0


class CallBody(BaseModel):
    arguments: dict[str, Any] = {}


class ApprovalRequest(BaseModel):
    server: str
    tool: str
    arguments: dict[str, Any]


class Decision(BaseModel):
    approved: bool


async def _target(entry: ServerEntry):
    url = entry.url()
    if not url:
        return entry.inproc()
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    token = await _identity.agent_token(MCP_SERVERS)
    return streamable_http_client(
        url, http_client=create_mcp_http_client(headers={"authorization": f"Bearer {token}"})
    )


async def discover(entry: ServerEntry, refresh: bool = False) -> list[dict[str, Any]]:
    hit = _DISCOVERY.get(entry.name)
    if hit and not refresh and hit[0] > time.monotonic():
        return hit[1]
    async with Client(await _target(entry)) as c:
        listed = await c.list_tools()
    tools = []
    for t in listed.tools:
        ann = t.annotations
        write = not (ann and ann.read_only_hint)
        tools.append(
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
                "output_schema": t.output_schema,
                "annotations": ann.model_dump(exclude_none=True) if ann else {},
                "governance": {
                    "write": write,
                    "allowed": (not write) or entry.writes_enabled,
                    "hitl": write,
                    "idempotency_required": write,
                },
            }
        )
    _DISCOVERY[entry.name] = (time.monotonic() + 60, tools)
    return tools


def _entry(server: str) -> ServerEntry:
    e = SERVERS.get(server)
    if e is None:
        raise E.GatewayError(E.NOT_FOUND, f"unknown MCP server {server}")
    return e


def _authorize(p: Principal, entry: ServerEntry, write: bool) -> None:
    if entry.name not in allowed_mcp_servers(p.actor):
        raise E.GatewayError(E.AUTHZ_DENY, f"agent card for {p.actor} does not allow MCP server {entry.name}")
    if p.is_user:
        raise E.GatewayError(
            E.AUTHZ_DENY, "MCP servers here are agent-scoped; use the Tool Gateway for user-scoped reads"
        )
    need = entry.write_role if write else entry.agent_role
    if need not in p.roles:
        raise E.GatewayError(E.AUTHZ_DENY, f"app role {need} not granted to {p.actor}")


@app.get("/v1/servers")
async def servers(p: Principal = Depends(auth)):
    return {
        "servers": [s.public() for s in SERVERS.values()],
        "allowed_for_caller": sorted(allowed_mcp_servers(p.actor)),
    }


@app.get("/v1/servers/{server}/tools")
async def tools(server: str, p: Principal = Depends(auth)):
    entry = _entry(server)
    _authorize(p, entry, write=False)
    return {"server": server, "tools": await discover(entry)}


@app.post("/v1/servers/{server}/tools/{tool}/call")
async def call(
    server: str,
    tool: str,
    body: CallBody,
    p: Principal = Depends(auth),
    idempotency_key: str | None = Header(default=None),
    x_approval_id: str | None = Header(default=None),
    traceparent: str | None = Header(default=None),
):
    entry = _entry(server)
    args = dict(body.arguments)
    bkey = str(
        args.get("business_key")
        or args.get("order_id")
        or args.get("delivery_id")
        or args.get("incident_id")
        or ""
    )
    base = {
        **p.audit_fields(),
        "gateway": "mcp",
        "operation": f"{server}.{tool}",
        "system": f"mcp:{server}",
        "business_key": bkey,
        "trace_id": trace_id(traceparent),
        "args_digest": digest(args),
    }
    with telemetry.integration_span(
        f"mcp {server}.{tool}",
        service=SERVICE,
        system=f"mcp:{server}",
        operation=f"{server}.{tool}",
        business_key=bkey,
        identity_mode=p.identity_mode,
        actor=p.actor,
        subject=p.subject,
        tenant=p.tenant_id,
        traceparent=traceparent,
    ) as span:
        try:
            out = await _call(entry, tool, args, p, idempotency_key, x_approval_id, span)
        except E.GatewayError as exc:
            span.set_result(exc.code)
            AUDIT.write(**base, result_class=exc.code, idempotency_key=idempotency_key)
            raise
        AUDIT.write(
            **base, result_class=E.OK, idempotency_key=idempotency_key, flags=len(out["screening"]["flags"])
        )
    return {**out, "server": server, "tool": tool, "result_class": E.OK, "trace_id": trace_id(traceparent)}


async def _call(
    entry: ServerEntry,
    tool: str,
    args: dict,
    p: Principal,
    idem_key: str | None,
    approval_id: str | None,
    span,
) -> dict:
    catalog = {t["name"]: t for t in await discover(entry)}
    meta = catalog.get(tool)
    if meta is None:
        raise E.GatewayError(E.NOT_FOUND, f"{entry.name} has no tool {tool}")
    write = meta["governance"]["write"]
    _authorize(p, entry, write)
    ok, retry = RATE.try_acquire(p.tenant_id)
    if not ok:
        raise E.GatewayError(E.RATE_LIMITED, "tenant rate limit exceeded", retry_after=retry)
    approval = None
    if write:
        if not entry.writes_enabled:
            raise E.GatewayError(E.AUTHZ_DENY, f"{entry.name} is read-only in the catalog")
        if not idem_key or len(idem_key) < 8:
            raise E.GatewayError(E.VALIDATION, "MCP writes need an Idempotency-Key header")
        slot = f"{p.tenant_id}:{entry.name}.{tool}:{idem_key}"
        if slot in IDEMPOTENCY:
            prior = IDEMPOTENCY[slot]
            if prior["digest"] != digest(args):
                raise E.GatewayError(E.CONFLICT, "idempotency key reused with different arguments")
            return {**prior["response"], "replayed": True}
        approval = APPROVALS.check(approval_id, f"{entry.name}.{tool}", args, p.tenant_id)
        args = {**args, "idempotency_key": idem_key}
    if errs := schema_errors(meta["input_schema"], args):
        raise E.GatewayError(
            E.VALIDATION, "arguments failed the server's input schema", detail={"fields": errs}
        )

    br = BREAKERS.setdefault(entry.name, CircuitBreaker(entry.name, failure_threshold=3, reset_timeout_s=15))
    try:
        br.before_call()
    except CircuitOpen as exc:
        raise E.GatewayError(
            E.UNAVAILABLE, f"{entry.name} circuit open", retry_after=exc.retry_after
        ) from exc
    try:
        async with Client(await _target(entry)) as c:
            result = await asyncio.wait_for(c.call_tool(tool, args), timeout=CALL_TIMEOUT_S)
    except TimeoutError:
        br.record_failure()
        raise E.GatewayError(E.TIMEOUT, f"{entry.name}.{tool} timed out") from None
    except Exception:
        br.record_failure()
        raise E.GatewayError(E.UNAVAILABLE, f"{entry.name} unreachable") from None
    br.record_success()
    if result.is_error:
        raise E.GatewayError(E.VALIDATION, "MCP server rejected the call")  # server text is not forwarded
    payload = result.structured_content
    if payload is None:
        try:
            payload = json.loads(result.content[0].text)
        except Exception:
            raise E.GatewayError(E.INTERNAL, "MCP server returned unstructured output") from None
    if meta["output_schema"] and (errs := schema_errors(meta["output_schema"], payload)):
        raise E.GatewayError(E.INTERNAL, "MCP output failed its declared schema", detail={"fields": errs[:5]})
    if isinstance(payload, dict) and payload.get("error"):
        code = {
            "authz_deny": E.AUTHZ_DENY,
            "not_found": E.NOT_FOUND,
            "business_reject": E.BUSINESS_REJECT,
            "timeout": E.TIMEOUT,
            "validation": E.VALIDATION,
        }.get(payload["error"], E.UNAVAILABLE)
        raise E.GatewayError(
            code,
            str(payload.get("message", ""))[:160]
            if code in {E.VALIDATION, E.BUSINESS_REJECT}
            else f"{entry.name} reported {payload['error']}",
        )
    clean, flags = screen(payload)
    span.set("untrusted_flags", len(flags))
    response = {"data": clean, "untrusted": True, "screening": {"flags": flags}, "replayed": False}
    if write:
        IDEMPOTENCY[f"{p.tenant_id}:{entry.name}.{tool}:{idem_key}"] = {
            "digest": digest({k: v for k, v in args.items() if k != "idempotency_key"}),
            "response": response,
        }
        if approval:
            APPROVALS.mark_used(approval)
            response["approved_by"] = approval.decided_by
    return response


@app.post("/v1/approvals")
async def request_approval(body: ApprovalRequest, p: Principal = Depends(auth)):
    entry = _entry(body.server)
    _authorize(p, entry, write=True)
    a = APPROVALS.request(
        f"{body.server}.{body.tool}",
        body.arguments,
        str(body.arguments.get("business_key", "")),
        p,
        entry.approver_role,
    )
    AUDIT.write(
        **p.audit_fields(),
        gateway="mcp",
        operation="approval.request",
        system=f"mcp:{body.server}",
        business_key=a.business_key,
        result_class=E.OK,
        approval_id=a.id,
    )
    return {"approval_id": a.id, "status": a.status, "approver_role": a.approver_role}


@app.post("/v1/approvals/{approval_id}/decision")
async def decide(approval_id: str, body: Decision, p: Principal = Depends(auth)):
    a = APPROVALS.decide(approval_id, body.approved, p)
    AUDIT.write(
        **p.audit_fields(),
        gateway="mcp",
        operation="approval.decision",
        system="hitl",
        business_key=a.business_key,
        result_class=E.OK,
        approval_id=a.id,
        decision=a.status,
    )
    return {"approval_id": a.id, "status": a.status, "decided_by": a.decided_by}


@app.get("/v1/audit")
async def audit(business_key: str | None = None, actor: str | None = None, p: Principal = Depends(auth)):
    return {
        "records": [
            r for r in AUDIT.query(business_key=business_key, actor=actor) if r.get("tenant") == p.tenant
        ][-500:],
        "chain_ok": AUDIT.verify_chain(),
    }


@app.get("/v1/metrics")
async def metrics_summary():
    return {**telemetry.LEDGER.summary(SERVICE), "breakers": {k: b.state for k, b in BREAKERS.items()}}


def reset_state() -> None:
    for x in (AUDIT, APPROVALS, RATE):
        x.reset()
    BREAKERS.clear()
    IDEMPOTENCY.clear()
    _DISCOVERY.clear()


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": SERVICE, "servers": sorted(SERVERS)}
