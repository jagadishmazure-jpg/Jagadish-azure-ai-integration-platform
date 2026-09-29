"""Local BPM host exposing the Durable Functions HTTP API shape, backed by LocalDurableRuntime.

POST /api/orchestrators/{name}                                           start (HTTP starter)
GET  /runtime/webhooks/durabletask/instances/{id}                        status
POST /runtime/webhooks/durabletask/instances/{id}/raiseEvent/{event}     external event (HITL)
POST /_local/instances/{id}/advance?hours=N                              move the virtual clock
GET  /v1/process-map/{process_id}                                        process id -> graph run ids

The ApprovalDecision event only wakes the process. Authority lives in the Tool Gateway approval
record decided by the human's own token: a forged event cannot post an invoice."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from aiip.bpm.activities import PROCESS_MAP
from aiip.bpm.local_runtime import LocalDurableRuntime
from aiip.bpm.orchestration import ORCHESTRATORS
from aiip.shared import errors as E
from aiip.shared.auth import local_mode_only

app = FastAPI(title="BPM host (Durable Functions stand-in)", version="1.0.0")
E.install_error_handlers(app)
RUNTIME = LocalDurableRuntime()
WEBHOOK = "/runtime/webhooks/durabletask/instances"


def _inst(instance_id: str):
    if instance_id not in RUNTIME.instances:
        raise E.GatewayError(E.NOT_FOUND, "unknown instance")
    return instance_id


@app.post("/api/orchestrators/{name}")
async def start(name: str, request: Request):
    if name not in ORCHESTRATORS:
        raise E.GatewayError(E.NOT_FOUND, f"unknown orchestrator {name}")
    body: dict[str, Any] = await request.json()
    iid = await RUNTIME.start(name, body, body.get("instance_id"))
    base = str(request.base_url).rstrip("/")
    return JSONResponse(
        {
            "id": iid,
            "statusQueryGetUri": f"{base}{WEBHOOK}/{iid}",
            "sendEventPostUri": f"{base}{WEBHOOK}/{iid}/raiseEvent/{{eventName}}",
        },
        status_code=202,
    )


@app.get(WEBHOOK + "/{instance_id}")
async def status(instance_id: str):
    return RUNTIME.status(_inst(instance_id))


@app.post(WEBHOOK + "/{instance_id}/raiseEvent/{event}")
async def raise_event(instance_id: str, event: str, request: Request):
    await RUNTIME.raise_event(_inst(instance_id), event, await request.json())
    return JSONResponse({"accepted": True}, status_code=202)


@app.post("/_local/instances/{instance_id}/advance")
async def advance(instance_id: str, hours: float = 1.0):
    local_mode_only()
    await RUNTIME.advance(_inst(instance_id), timedelta(hours=hours))
    return RUNTIME.status(instance_id)


@app.get("/v1/process-map/{process_id}")
async def process_map(process_id: str):
    if process_id not in PROCESS_MAP:
        raise E.GatewayError(E.NOT_FOUND, "unknown process")
    return {"process_id": process_id, **PROCESS_MAP[process_id]}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "bpm-host", "orchestrators": sorted(ORCHESTRATORS)}
