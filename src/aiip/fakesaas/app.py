"""The sandbox SaaS host. Every response is tagged `x-aiip-sandbox-standin: true`.
`/_admin/*` injects faults (slow, error, rate_limit) for failure drills and resets state."""

from __future__ import annotations

from fastapi import FastAPI, Request
from pydantic import BaseModel

from aiip.fakesaas import dataverse, jira, salesforce, sap, servicenow, state, warehouse, workday

app = FastAPI(title="Sandbox SaaS stand-ins (NOT vendor products)", version="1.0.0")
for r in (
    salesforce.router,
    sap.router,
    servicenow.router,
    workday.router,
    dataverse.router,
    jira.router,
    warehouse.router,
):
    app.include_router(r)


@app.middleware("http")
async def tag(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["x-aiip-sandbox-standin"] = "true"
    return resp


class Fault(BaseModel):
    mode: str  # slow | error | rate_limit
    count: int = 1
    delay_s: float = 2.0


@app.get("/")
async def root():
    return {
        "service": "sandbox-saas",
        "standin": True,
        "note": "Local stand-ins with vendor-shaped APIs and fictional data; not Salesforce, SAP, ServiceNow, Workday, Microsoft or Atlassian software.",
        "vendors": ["salesforce", "sap", "servicenow", "workday", "dataverse", "jira", "databricks"],
    }


@app.post("/_admin/faults/{vendor}")
async def set_fault(vendor: str, fault: Fault):
    state.FAULTS[vendor] = {"mode": fault.mode, "remaining": fault.count, "delay_s": fault.delay_s}
    return {"vendor": vendor, **state.FAULTS[vendor]}


@app.delete("/_admin/faults")
async def clear_faults():
    state.FAULTS.clear()
    return {"cleared": True}


@app.post("/_admin/reset")
async def reset():
    state.reset()
    return {"reset": True}


@app.get("/_admin/state/{vendor}")
async def peek(vendor: str):
    """Read-only peek used by the demo to prove 'no double post'."""
    data = state.DATA.get(vendor, {})
    return {k: v for k, v in data.items() if k not in {"csrf", "idempotency", "users"}}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "sandbox-saas"}
