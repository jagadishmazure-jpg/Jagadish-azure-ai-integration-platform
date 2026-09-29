"""A2A Gateway (FastAPI) + agent directory.

GET  /v1/agents                       directory: every card with owner, SLA, side effects, eval score
GET  /v1/agents/{id}/card?version=    full A2A 1.0 card (JSON)
POST /v1/agents/{id}/a2a              governed JSON-RPC proxy to the resolved agent version

Proxy checks: token audience is the callee's app ID URI; the caller (token azp) is on the callee's
allow-list; hop cap; tenant header equals token tenant; A2A-Version 1.0; method + skill exist in the
resolved version; skill input matches the card schema. Forwards a child traceparent, tenant and hop
count. The peer's artifact is screened as untrusted before it is returned."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, Request

from aiip.a2a import directory
from aiip.a2a.cards import card_json
from aiip.a2a.specs import AGENTS
from aiip.identity.registrations import agent_uri
from aiip.safety.killswitch import KILL
from aiip.shared import errors as E
from aiip.shared import http, telemetry
from aiip.shared.audit import AuditLog
from aiip.shared.auth import bearer, validate_token
from aiip.shared.schema import errors as schema_errors
from aiip.shared.tracecontext import child_traceparent, new_traceparent, trace_id, valid
from aiip.shared.untrusted import screen

app = FastAPI(title="A2A Gateway", version="1.0.0")
E.install_error_handlers(app)
SERVICE = "a2a-gateway"
AUDIT = AuditLog("a2a-gateway")
METHODS = {"SendMessage", "GetTask", "CancelTask"}


@app.get("/v1/agents")
async def agents():
    return {"agents": directory.listing(), "max_hops": directory.MAX_HOPS}


@app.get("/v1/agents/{agent_id}/card")
async def card(agent_id: str, version: str | None = None):
    return card_json(directory.resolve(agent_id, version))


@app.post("/v1/agents/{agent_id}/a2a")
async def proxy(
    agent_id: str,
    request: Request,
    a2a_version: str | None = Header(default=None),
    traceparent: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
    x_a2a_hops: str | None = Header(default=None),
    x_agent_version: str | None = Header(default=None),
    x_agent_session: str | None = Header(default=None),
):
    spec = directory.resolve(agent_id, x_agent_version)
    p = await validate_token(bearer(request), agent_uri(agent_id))
    tp = child_traceparent(traceparent) if valid(traceparent) else new_traceparent()
    body: dict[str, Any] = await request.json()
    parts = body.get("params", {}).get("message", {}).get("parts", [])
    data = parts[0].get("data", {}) if parts and isinstance(parts[0], dict) else {}
    skill_id = data.get("skill", "")
    with telemetry.integration_span(
        f"a2a {agent_id}.{skill_id}",
        service=SERVICE,
        system=f"a2a:{agent_id}",
        operation=f"{agent_id}.{skill_id}",
        business_key=str(next(iter((data.get("input") or {}).values()), "")) if data.get("input") else "",
        identity_mode=p.identity_mode,
        actor=p.actor,
        subject=p.subject,
        tenant=p.tenant_id,
        traceparent=tp,
        callee_version=spec.version,
    ) as span:
        audit = {
            **p.audit_fields(),
            "gateway": "a2a",
            "operation": f"{agent_id}.{skill_id}",
            "callee_version": spec.version,
            "trace_id": trace_id(tp),
            "session": x_agent_session,
        }
        try:
            KILL.enforce(p.tenant, p.actor, x_agent_session)  # runtime-safety quarantine
            if a2a_version != "1.0":
                raise E.GatewayError(E.VALIDATION, "A2A-Version: 1.0 header required")
            directory.authorize_caller(p.actor, spec)
            hops = directory.next_hop(x_a2a_hops)
            if x_tenant_id and x_tenant_id != p.tenant_id:
                raise E.GatewayError(E.AUTHZ_DENY, "tenant header does not match token")
            if body.get("method") not in METHODS:
                raise E.GatewayError(E.VALIDATION, "unsupported JSON-RPC method")
            skill = spec.skill(skill_id)
            if skill is None:
                raise E.GatewayError(E.NOT_FOUND, f"{spec.key} has no skill {skill_id}")
            if errs := schema_errors(skill.input_schema, data.get("input") or {}):
                raise E.GatewayError(
                    E.VALIDATION, "skill input failed the card schema", detail={"fields": errs}
                )
            headers = {
                "authorization": request.headers["authorization"],
                "A2A-Version": "1.0",
                "traceparent": tp,
                "x-tenant-id": p.tenant_id,
                "x-caller-agent": p.actor,
                "x-a2a-hops": str(hops),
                "content-type": "application/json",
            }
            async with http.client(spec.service, timeout=spec.sla_p95_ms / 1000 * 5) as c:
                resp = await c.post(spec.rpc_path, json=body, headers=headers)
            if resp.status_code >= 500:
                raise E.GatewayError(E.UNAVAILABLE, f"{spec.key} returned HTTP {resp.status_code}")
            rpc = resp.json()
            if "error" in rpc:
                raise E.GatewayError(E.VALIDATION, "peer rejected the JSON-RPC request")
            out_parts = rpc.get("result", {}).get("message", {}).get("parts", [])
            artifact = out_parts[0].get("data") if out_parts else None
            if not isinstance(artifact, dict):
                raise E.GatewayError(E.INTERNAL, "peer returned no data artifact")
            clean, flags = screen(artifact)
            if "error" in artifact:
                span.set_result(artifact["error"] if artifact["error"] in E.HTTP_STATUS else E.INTERNAL)
        except E.GatewayError as exc:
            span.set_result(exc.code)
            AUDIT.write(**audit, result_class=exc.code)
            raise
        AUDIT.write(**audit, result_class=span.result_class, hops=hops, untrusted_flags=len(flags))
    return {
        "artifact": clean,
        "screening": {"flags": flags},
        "resolved_version": spec.version,
        "trace_id": trace_id(tp),
        "hops": hops,
    }


@app.get("/v1/audit")
async def audit(actor: str | None = None, subject: str | None = None):
    return {"records": AUDIT.query(actor=actor, subject=subject)[-500:], "chain_ok": AUDIT.verify_chain()}


@app.get("/v1/metrics")
async def metrics_summary():
    return telemetry.LEDGER.summary(SERVICE)


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": SERVICE, "agents": sorted({a.id for a in AGENTS if a.kind == "a2a-agent"})}
