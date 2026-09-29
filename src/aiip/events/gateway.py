"""Event Gateway (FastAPI): the edge where canonical business events enter the platform.

POST /v1/events            publish one CloudEvent or a batch (publisher needs Events.Publish)
OPTIONS /v1/events         CloudEvents webhook abuse-protection handshake (Event Grid)
GET  /v1/events/schemas    canonical event catalog
GET  /v1/metrics           admission outcomes + queue depths (incl. dead-letter and parked)

Admission, per event: envelope + data schema -> tenant must match the publisher's token ->
dedupe on (tenant, type, business key) within a window -> per-tenant, per-event-class budget
(excess is parked, not dropped, so an event storm cannot fan out into unbounded agent runs) ->
route to the class queue (Event Grid -> Service Bus in Azure; in-memory stand-in locally).

Local mode also exposes /bus/* (peek-lock receive / complete / abandon / dead-letter) so workers
run as separate processes against the stand-in exactly as they would against Service Bus."""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from aiip.config import is_azure
from aiip.events.bus import EventGridPublisher, InMemoryBus, Message
from aiip.events.canonical import ENVELOPE_SCHEMA, EVENT_CLASSES
from aiip.identity.registrations import EVENT_GW
from aiip.shared import errors as E
from aiip.shared import telemetry
from aiip.shared.audit import AuditLog
from aiip.shared.auth import Principal, local_mode_only, principal_dependency
from aiip.shared.resilience import TokenBucket
from aiip.shared.schema import errors as schema_errors

app = FastAPI(title="Event Gateway", version="1.0.0")
E.install_error_handlers(app)
auth = principal_dependency(EVENT_GW)
SERVICE = "event-gateway"
AUDIT = AuditLog("event-gateway")
BUS = InMemoryBus()
DEDUP_WINDOW_S = 600.0
_seen: dict[str, float] = {}
_budgets: dict[str, TokenBucket] = {}
_outcomes: dict[str, dict[str, int]] = {}
_publisher: EventGridPublisher | None = None
telemetry.register_queue_depth("local-bus", BUS.depths)


def _budget(ec) -> TokenBucket:
    if ec.type not in _budgets:
        _budgets[ec.type] = TokenBucket(
            capacity=ec.budget_per_minute, refill_per_s=ec.budget_per_minute / 60.0
        )
    return _budgets[ec.type]


def _count(name: str, outcome: str) -> None:
    _outcomes.setdefault(name, {}).setdefault(outcome, 0)
    _outcomes[name][outcome] += 1


async def admit(event: dict[str, Any], p: Principal) -> dict[str, Any]:
    if errs := schema_errors(ENVELOPE_SCHEMA, event):
        raise E.GatewayError(E.VALIDATION, "not a valid canonical CloudEvent", detail={"fields": errs})
    ec = EVENT_CLASSES.get(event["type"])
    if ec is None:
        raise E.GatewayError(E.VALIDATION, f"unknown event type {event['type']}")
    if errs := schema_errors(ec.schema, event["data"]):
        raise E.GatewayError(E.VALIDATION, f"{ec.name} data failed its schema", detail={"fields": errs})
    if event["tenantid"] != p.tenant_id:
        raise E.GatewayError(E.AUTHZ_DENY, "publisher may only publish for its own tenant")
    key = str(event["data"][ec.business_key])
    if event.get("businesskey", key) != key or event["subject"] != key:
        raise E.GatewayError(E.VALIDATION, "subject/businesskey must equal the business key")
    with telemetry.integration_span(
        f"event {ec.name}",
        service=SERVICE,
        system="event-grid",
        operation=ec.name,
        business_key=key,
        identity_mode=p.identity_mode,
        actor=p.actor,
        subject=p.subject,
        tenant=p.tenant_id,
        traceparent=event.get("traceparent"),
    ) as span:
        now = time.monotonic()
        dedup_key = f"{p.tenant_id}|{ec.type}|{key}"
        if _seen.get(dedup_key, 0) > now:
            outcome = "duplicate"
        else:
            ok, _ = _budget(ec).try_acquire(p.tenant_id)
            msg = Message(
                event["id"],
                event,
                {
                    "type": ec.type,
                    "tenant": p.tenant_id,
                    "businesskey": key,
                    "traceparent": event.get("traceparent", ""),
                },
            )
            if not ok:
                BUS.park(ec.queue, msg, f"budget exceeded for {ec.name}")
                outcome = "parked_budget"
            else:
                _seen[dedup_key] = now + DEDUP_WINDOW_S
                if is_azure():  # pragma: no cover
                    global _publisher
                    _publisher = _publisher or EventGridPublisher()
                    await _publisher.publish(event)
                else:
                    BUS.send(ec.queue, msg)
                outcome = "accepted"
        span.set("admission", outcome)
        if outcome != "accepted":
            span.set_result(E.BUDGET if outcome == "parked_budget" else E.OK)
    _count(ec.name, outcome)
    AUDIT.write(
        **p.audit_fields(),
        gateway="event",
        operation=ec.name,
        system="event-grid",
        business_key=key,
        event_id=event["id"],
        result_class=outcome,
    )
    return {"id": event["id"], "type": ec.name, "business_key": key, "status": outcome, "queue": ec.queue}


@app.post("/v1/events")
async def publish(request: Request, p: Principal = Depends(auth)):
    if "Events.Publish" not in p.roles:
        raise E.GatewayError(E.AUTHZ_DENY, "publisher lacks Events.Publish")
    body = await request.json()
    events = body if isinstance(body, list) else [body]
    if len(events) > 100:
        raise E.GatewayError(E.VALIDATION, "batch too large (max 100)")
    return {"results": [await admit(e, p) for e in events]}


@app.options("/v1/events")
async def webhook_handshake(request: Request):
    """CloudEvents v1.0 webhook validation used by Event Grid push delivery."""
    origin = request.headers.get("webhook-request-origin", "")
    return Response(
        status_code=200, headers={"WebHook-Allowed-Origin": origin or "*", "WebHook-Allowed-Rate": "120"}
    )


@app.get("/v1/events/schemas")
async def schemas():
    return {
        "events": [
            {
                "name": e.name,
                "type": e.type,
                "queue": e.queue,
                "business_key": e.business_key,
                "budget_per_minute": e.budget_per_minute,
                "schema": e.schema,
            }
            for e in EVENT_CLASSES.values()
        ]
    }


@app.get("/v1/metrics")
async def metrics_summary():
    return {"admission": _outcomes, "queues": BUS.stats(), **telemetry.LEDGER.summary(SERVICE)}


# ------------------------------------------------------------- local Service Bus stand-in
class LockBody(BaseModel):
    lock_token: str
    reason: str = ""
    description: str = ""


@app.post("/bus/{queue}/receive")
async def bus_receive(queue: str, max_messages: int = 1):
    local_mode_only()
    return {"messages": [m.public() for m in BUS.receive(queue, max_messages)]}


@app.post("/bus/{queue}/complete")
async def bus_complete(queue: str, body: LockBody):
    local_mode_only()
    BUS.complete(queue, body.lock_token)
    return {"ok": True}


@app.post("/bus/{queue}/abandon")
async def bus_abandon(queue: str, body: LockBody):
    local_mode_only()
    BUS.abandon(queue, body.lock_token)
    return {"ok": True}


@app.post("/bus/{queue}/deadletter")
async def bus_deadletter(queue: str, body: LockBody):
    local_mode_only()
    BUS.dead_letter(queue, body.lock_token, body.reason, body.description)
    return {"ok": True}


@app.get("/bus/stats")
async def bus_stats():
    return BUS.stats()


@app.get("/bus/{queue}/deadletter")
async def bus_dlq(queue: str):
    return {"messages": [m.public() for m in BUS.dlq.get(queue, [])]}


@app.get("/bus/{queue}/peek")
async def bus_peek(queue: str):
    return {"messages": [m.public() for m in BUS.active.get(queue, [])]}


def reset_state() -> None:
    BUS.reset()
    _seen.clear()
    _budgets.clear()
    _outcomes.clear()
    AUDIT.reset()


@app.get("/healthz")
async def healthz():
    return JSONResponse({"ok": True, "service": SERVICE, "event_types": len(EVENT_CLASSES)})
