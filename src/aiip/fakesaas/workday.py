"""Workday REST stand-in (`/ccx/api/v1/{tenant}`): OAuth refresh-token grant for an integration
system user, worker lookup. Worker records deliberately include sensitive fields so the connector's
data-minimisation can be tested."""

from __future__ import annotations

from fastapi import APIRouter, Form, Header
from fastapi.responses import JSONResponse

from aiip.fakesaas import state
from aiip.shared.secrets import RESOLVER

router = APIRouter(prefix="/workday")
REFRESH_TOKEN_REF = "kv://aiip-local-kv/workday-isu-refresh-token"


@router.post("/ccx/oauth2/{tenant}/token")
async def token(
    tenant: str, grant_type: str = Form(...), refresh_token: str = Form(...), client_id: str = Form(...)
):
    await state.apply_fault("workday")
    if grant_type != "refresh_token" or refresh_token != RESOLVER.resolve(REFRESH_TOKEN_REF):
        return JSONResponse({"error": "invalid_grant"}, 400)
    return {
        "access_token": state.issue_token("workday", f"ISU_{client_id[:8]}", tenant=tenant),
        "token_type": "Bearer",
    }


@router.get("/ccx/api/v1/{tenant}/workers/{worker_id}")
async def get_worker(tenant: str, worker_id: str, authorization: str | None = Header(default=None)):
    await state.apply_fault("workday")
    state.token_info("workday", authorization)
    w = state.DATA["workday"]["workers"].get(worker_id)
    if not w:
        return JSONResponse(
            {"error": "invalid resource id", "errors": [{"error": f"worker {worker_id} not found"}]}, 404
        )
    return w


@router.get("/ccx/api/v1/{tenant}/workers")
async def search_workers(
    tenant: str,
    costCenter: str = "",
    isManager: bool | None = None,
    authorization: str | None = Header(default=None),
):
    await state.apply_fault("workday")
    state.token_info("workday", authorization)
    rows = [
        w
        for w in state.DATA["workday"]["workers"].values()
        if (not costCenter or w["costCenter"]["id"] == costCenter)
        and (isManager is None or w["isManager"] == isManager)
    ]
    return {"total": len(rows), "data": rows}
