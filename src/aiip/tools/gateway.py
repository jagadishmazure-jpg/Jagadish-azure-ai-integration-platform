"""Tool Gateway (FastAPI).

POST /v1/tools/{name}/invoke     the only way an agent reaches a system of record
GET  /v1/tools                   catalog, filtered to the caller's agent-card allow-list
POST /v1/approvals               request a human approval for a HITL commit
POST /v1/approvals/{id}/decision record the human decision (user token with the approver role)
GET  /v1/audit                   who did what to which business key, when
GET  /v1/metrics                 measured success by system, p95 by tool, identity mix
GET  /v1/breakers                circuit-breaker states

Invoke pipeline, in order: authenticate -> tool lookup -> agent-card allow-list -> identity policy
(as whom?) -> per-tenant rate limit -> input schema -> idempotency / approval (commits) -> cache
(safe reads) -> circuit breaker + SaaS back-off -> connector call with timeout -> result class ->
output schema -> audit + span. Errors leaving the gateway are sanitized."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from fastapi import Depends, FastAPI, Header, Request
from pydantic import BaseModel

from aiip.a2a.specs import allowed_tools
from aiip.connectors.base import ConnectorContext, ConnectorError
from aiip.connectors.registry import resolve
from aiip.identity.registrations import TOOL_GW
from aiip.shared import errors as E
from aiip.shared import telemetry
from aiip.shared.approvals import ApprovalStore
from aiip.shared.audit import AuditLog, digest
from aiip.shared.auth import Principal, local_mode_only, principal_dependency
from aiip.shared.resilience import CircuitBreaker, CircuitOpen, TokenBucket, TTLCache
from aiip.shared.schema import errors as schema_errors
from aiip.shared.tracecontext import trace_id
from aiip.tools.registry import TOOLS, ToolDef

app = FastAPI(title="Tool Gateway", version="1.0.0")
E.install_error_handlers(app)
auth = principal_dependency(TOOL_GW)

SERVICE = "tool-gateway"
AUDIT = AuditLog("tool-gateway")
APPROVALS = ApprovalStore()
CACHE = TTLCache(ttl_s=30)
RATE = TokenBucket(
    capacity=float(os.environ.get("AIIP_TENANT_BURST", 40)),
    refill_per_s=float(os.environ.get("AIIP_TENANT_RPS", 20)),
)
BREAKERS: dict[str, CircuitBreaker] = {}
IDEMPOTENCY: dict[str, dict[str, Any]] = {}
VENDOR_BACKOFF: dict[str, float] = {}
APPROVER_ROLE = {"erp.post_parked_invoice": "APApprover", "erp.create_sales_order": "CareRep"}


def breaker(system: str) -> CircuitBreaker:
    if system not in BREAKERS:
        BREAKERS[system] = CircuitBreaker(
            system,
            failure_threshold=int(os.environ.get("AIIP_BREAKER_FAILURES", 3)),
            reset_timeout_s=float(os.environ.get("AIIP_BREAKER_RESET_S", 15)),
        )
    return BREAKERS[system]


class InvokeBody(BaseModel):
    args: dict[str, Any]


class ApprovalRequest(BaseModel):
    tool: str
    args: dict[str, Any]


class Decision(BaseModel):
    approved: bool


def _authorize(p: Principal, tool: ToolDef) -> None:
    if tool.name not in allowed_tools(p.actor):
        raise E.GatewayError(E.AUTHZ_DENY, f"agent card for {p.actor} does not allow {tool.name}")
    if p.is_user:
        if tool.identity == "agent_only":
            raise E.GatewayError(E.AUTHZ_DENY, f"{tool.name} runs under an agent identity only")
        if "user_impersonation" not in p.scopes:
            raise E.GatewayError(E.AUTHZ_DENY, "delegated token lacks the required scope")
    else:
        if tool.identity == "user_required":
            raise E.GatewayError(E.AUTHZ_DENY, f"{tool.name} must run as the user (on-behalf-of)")
        if tool.app_role not in p.roles:
            raise E.GatewayError(E.AUTHZ_DENY, f"app role {tool.app_role} not granted to {p.actor}")


def needs_approval(tool: ToolDef, args: dict) -> bool:
    if tool.hitl_above is None:
        return True
    try:
        return float(args.get("amount", 0)) > tool.hitl_above
    except (TypeError, ValueError):
        return True


def _safe_message(exc: ConnectorError) -> str:
    """Vendor text is only passed through where it helps the agent fix its request."""
    if exc.error_class in {"business_reject", "validation"}:
        return exc.message[:200]
    return {
        "authz_deny": "the principal is not entitled to this record",
        "not_found": "record not found",
        "rate_limited": "system is rate limiting; retry later",
        "timeout": "system did not answer in time",
        "transient": "system temporarily unavailable",
        "conflict": "conflicting update",
        "auth_expired": "integration credentials rejected",
    }.get(exc.error_class, "integration error")


@app.post("/v1/tools/{name}/invoke")
async def invoke(
    name: str,
    body: InvokeBody,
    request: Request,
    p: Principal = Depends(auth),
    idempotency_key: str | None = Header(default=None),
    x_approval_id: str | None = Header(default=None),
    traceparent: str | None = Header(default=None),
):
    tool = TOOLS.get(name)
    if tool is None:
        raise E.GatewayError(E.NOT_FOUND, f"unknown tool {name}")
    args = body.args
    bkey = str(args.get(tool.business_key, ""))
    audit_base = {
        **p.audit_fields(),
        "gateway": "tool",
        "operation": name,
        "system": tool.system,
        "side_effect": tool.side_effect,
        "business_key": bkey,
        "trace_id": trace_id(traceparent),
        "args_digest": digest(args),
    }
    with telemetry.integration_span(
        f"tool {name}",
        service=SERVICE,
        system=tool.system,
        operation=name,
        business_key=bkey,
        identity_mode=p.identity_mode,
        actor=p.actor,
        subject=p.subject,
        tenant=p.tenant_id,
        traceparent=traceparent,
        side_effect=tool.side_effect,
    ) as span:
        try:
            result = await _pipeline(tool, args, p, idempotency_key, x_approval_id, bkey, span)
        except E.GatewayError as exc:
            span.set_result(exc.code)
            AUDIT.write(
                **audit_base,
                result_class=exc.code,
                idempotency_key=idempotency_key,
                approval_id=x_approval_id,
            )
            raise
        rec = AUDIT.write(
            **audit_base,
            result_class=E.OK,
            idempotency_key=idempotency_key,
            approval_id=x_approval_id,
            replayed=result.get("replayed", False),
            cache=result.get("cache"),
        )
    return {
        **result,
        "tool": name,
        "result_class": E.OK,
        "trace_id": trace_id(traceparent),
        "audit_id": rec["hash"],
    }


async def _pipeline(
    tool: ToolDef, args: dict, p: Principal, idem_key: str | None, approval_id: str | None, bkey: str, span
) -> dict:
    _authorize(p, tool)
    ok, retry = RATE.try_acquire(p.tenant_id)
    if not ok:
        raise E.GatewayError(E.RATE_LIMITED, "tenant rate limit exceeded", retry_after=retry)
    if errs := schema_errors(tool.input_schema, args):
        raise E.GatewayError(
            E.VALIDATION, "arguments failed the tool's input schema", detail={"fields": errs}
        )

    approval = None
    idem_slot = None
    if tool.side_effect == "commit":
        if not idem_key or len(idem_key) < 8:
            raise E.GatewayError(E.VALIDATION, "commits need an Idempotency-Key header (>= 8 chars)")
        idem_slot = f"{p.tenant_id}:{tool.name}:{idem_key}"
        prior = IDEMPOTENCY.get(idem_slot)
        if prior:
            if prior["args_digest"] != digest(args):
                raise E.GatewayError(E.CONFLICT, "idempotency key reused with different arguments")
            span.set("replayed", True)
            return {**prior["response"], "replayed": True, "replay_source": "gateway"}
        if tool.hitl and (approval_id or needs_approval(tool, args)):
            approval = APPROVALS.check(approval_id, tool.name, args, p.tenant_id)

    cache_key = None
    if tool.side_effect == "read" and tool.cache_ttl_s:
        cache_key = f"{p.tenant_id}|{p.subject_id}|{p.identity_mode}|{tool.name}|{digest(args)}"  # subject-scoped: ACLs survive caching
        if (hit := CACHE.get(cache_key)) is not None:
            span.set("cache", "hit")
            return {"data": hit, "cache": "hit", "replayed": False}

    br = breaker(tool.system)
    try:
        br.before_call()
    except CircuitOpen as exc:
        raise E.GatewayError(
            E.UNAVAILABLE, f"{tool.system} circuit open; failing fast", retry_after=exc.retry_after
        ) from exc
    if VENDOR_BACKOFF.get(tool.system, 0) > time.monotonic():
        raise E.GatewayError(
            E.RATE_LIMITED,
            f"{tool.system} asked us to back off",
            retry_after=VENDOR_BACKOFF[tool.system] - time.monotonic(),
        )

    pack, op = resolve(tool.connector)
    ctx = ConnectorContext(principal=p, idempotency_key=idem_key, timeout_s=tool.timeout_s, business_key=bkey)
    try:
        res = await asyncio.wait_for(pack.invoke(op, args, ctx), timeout=tool.timeout_s + 1)
    except (TimeoutError, ConnectorError) as exc:
        cerr = exc if isinstance(exc, ConnectorError) else ConnectorError("timeout", "tool budget exceeded")
        if cerr.error_class in {"transient", "timeout"}:
            br.record_failure()
        else:
            br.record_success()  # the system answered; a business or authz refusal is not an outage
        if cerr.error_class == "rate_limited" and cerr.retry_after:
            VENDOR_BACKOFF[tool.system] = time.monotonic() + cerr.retry_after
        span.set("integration.vendor_code", cerr.vendor_code or None)
        raise E.GatewayError(
            cerr.result_class,
            _safe_message(cerr),
            detail={"vendor_code": cerr.vendor_code} if cerr.vendor_code else None,
            retry_after=cerr.retry_after,
        ) from None
    br.record_success()

    if errs := schema_errors(tool.output_schema, res.data):
        raise E.GatewayError(
            E.INTERNAL, "system response failed the tool's output schema", detail={"fields": errs[:5]}
        )

    response: dict[str, Any] = {
        "data": res.data,
        "replayed": res.replayed,
        "cache": "miss" if cache_key else None,
    }
    if res.replayed:
        response["replay_source"] = "vendor"
    if res.hint:
        response["rate_limit_hint"] = {
            "remaining": res.hint.remaining,
            "limit": res.hint.limit,
            "source": res.hint.source,
        }
    if cache_key:
        CACHE.set(cache_key, res.data, tool.cache_ttl_s)
    if idem_slot:
        IDEMPOTENCY[idem_slot] = {
            "args_digest": digest(args),
            "response": {k: v for k, v in response.items() if k != "replayed"},
        }
    if approval:
        APPROVALS.mark_used(approval)
        response["approved_by"] = approval.decided_by
    return response


@app.get("/v1/tools")
async def catalog(p: Principal = Depends(auth)):
    allowed = allowed_tools(p.actor)
    return {"actor": p.actor, "tools": [t.public() for t in TOOLS.values() if t.name in allowed]}


@app.get("/v1/tools/{name}")
async def tool_detail(name: str, p: Principal = Depends(auth)):
    t = TOOLS.get(name)
    if t is None or name not in allowed_tools(p.actor):
        raise E.GatewayError(E.NOT_FOUND, "unknown tool")
    return t.public()


@app.post("/v1/approvals")
async def request_approval(body: ApprovalRequest, p: Principal = Depends(auth)):
    tool = TOOLS.get(body.tool)
    if tool is None or not tool.hitl:
        raise E.GatewayError(E.VALIDATION, "tool does not take approvals")
    _authorize(p, tool)
    a = APPROVALS.request(
        tool.name,
        body.args,
        str(body.args.get(tool.business_key, "")),
        p,
        APPROVER_ROLE.get(tool.name, "Approver"),
    )
    AUDIT.write(
        **p.audit_fields(),
        gateway="tool",
        operation="approval.request",
        system=tool.system,
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
        gateway="tool",
        operation="approval.decision",
        system="hitl",
        business_key=a.business_key,
        result_class=E.OK,
        approval_id=a.id,
        decision=a.status,
    )
    return {"approval_id": a.id, "status": a.status, "decided_by": a.decided_by}


@app.get("/v1/approvals/{approval_id}")
async def approval_status(approval_id: str, p: Principal = Depends(auth)):
    a = APPROVALS.items.get(approval_id)
    if a is None or a.tenant_id != p.tenant_id:
        raise E.GatewayError(E.NOT_FOUND, "unknown approval")
    return {
        "approval_id": a.id,
        "status": a.status,
        "tool": a.tool,
        "business_key": a.business_key,
        "decided_by": a.decided_by,
    }


@app.get("/v1/audit")
async def audit(
    business_key: str | None = None,
    actor: str | None = None,
    subject: str | None = None,
    p: Principal = Depends(auth),
):
    rows = [
        r
        for r in AUDIT.query(business_key=business_key, actor=actor, subject=subject)
        if r.get("tenant") == p.tenant
    ]
    return {"records": rows[-500:], "chain_ok": AUDIT.verify_chain()}


@app.get("/v1/metrics")
async def metrics_summary():
    return {
        **telemetry.LEDGER.summary(SERVICE),
        "breakers": {k: b.state for k, b in BREAKERS.items()},
        "cache": {"hits": CACHE.hits, "misses": CACHE.misses},
    }


@app.get("/v1/breakers")
async def breakers():
    return {
        k: {"state": b.state, "failures": b.failures, "transitions": b.transitions}
        for k, b in BREAKERS.items()
    }


@app.post("/v1/admin/reset")
async def admin_reset(scope: str = "all"):
    """Local drills only: reset breakers/cache, or simulate a gateway restart (idempotency store lost)."""
    local_mode_only()
    if scope in {"all", "breakers"}:
        BREAKERS.clear()
        VENDOR_BACKOFF.clear()
    if scope in {"all", "cache"}:
        CACHE.reset()
    if scope in {"all", "restart"}:
        IDEMPOTENCY.clear()
    if scope == "all":
        RATE.reset()
    return {"reset": scope}


def reset_state() -> None:
    for x in (AUDIT, APPROVALS, CACHE, RATE):
        x.reset()
    BREAKERS.clear()
    IDEMPOTENCY.clear()
    VENDOR_BACKOFF.clear()


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": SERVICE, "tools": len(TOOLS)}
