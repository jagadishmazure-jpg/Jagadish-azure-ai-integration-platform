"""ServiceNow Table API stand-in: OAuth client credentials, incident read/query/insert,
X-RateLimit-* headers, 429 + Retry-After under injected faults."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Form, Header, Request, Response
from fastapi.responses import JSONResponse

from aiip.fakesaas import state
from aiip.shared.secrets import RESOLVER

router = APIRouter(prefix="/servicenow")
CLIENT_SECRET_REF = "kv://aiip-local-kv/servicenow-oauth-client-secret"
LIMIT = 500


def _limits(resp: Response) -> None:
    resp.headers["X-RateLimit-Limit"] = str(LIMIT)
    resp.headers["X-RateLimit-Remaining"] = str(max(0, LIMIT - state.CALLS.get("servicenow", 0)))


@router.post("/oauth_token.do")
async def token(grant_type: str = Form(...), client_id: str = Form(...), client_secret: str = Form(...)):
    await state.apply_fault("servicenow")
    if grant_type != "client_credentials" or client_secret != RESOLVER.resolve(CLIENT_SECRET_REF):
        return JSONResponse({"error": "access_denied", "error_description": "invalid client"}, 401)
    return {
        "access_token": state.issue_token("servicenow", client_id),
        "token_type": "Bearer",
        "expires_in": 1799,
    }


@router.get("/api/now/table/incident")
async def list_incidents(
    response: Response,
    sysparm_query: str = "",
    sysparm_limit: int = 10,
    sysparm_fields: str = "",
    authorization: str | None = Header(default=None),
):
    await state.apply_fault("servicenow")
    state.token_info("servicenow", authorization)
    _limits(response)
    rows = list(state.DATA["servicenow"]["incidents"].values())
    for clause in [c for c in sysparm_query.split("^") if "=" in c]:
        k, v = clause.split("=", 1)
        rows = [r for r in rows if str(r.get(k, "")) == v]
    fields = [f for f in sysparm_fields.split(",") if f]
    if fields:
        rows = [{f: r.get(f) for f in fields} for r in rows]
    return {"result": rows[:sysparm_limit]}


@router.get("/api/now/table/incident/{sys_id}")
async def get_incident(sys_id: str, response: Response, authorization: str | None = Header(default=None)):
    await state.apply_fault("servicenow")
    state.token_info("servicenow", authorization)
    _limits(response)
    row = state.DATA["servicenow"]["incidents"].get(sys_id)
    if not row:
        return JSONResponse(
            {"error": {"message": "No Record found", "detail": "Record doesn't exist"}, "status": "failure"},
            404,
        )
    return {"result": row}


@router.post("/api/now/table/incident")
async def create_incident(
    request: Request, response: Response, authorization: str | None = Header(default=None)
):
    await state.apply_fault("servicenow")
    info = state.token_info("servicenow", authorization)
    _limits(response)
    body = await request.json()
    if not body.get("short_description"):
        return JSONResponse(
            {"error": {"message": "Mandatory field missing: short_description"}, "status": "failure"}, 400
        )
    incidents = state.DATA["servicenow"]["incidents"]
    sys_id = secrets.token_hex(16)
    row = {
        "sys_id": sys_id,
        "number": f"INC00{10001 + len(incidents)}",
        "short_description": body["short_description"],
        "description": body.get("description", ""),
        "state": "1",
        "priority": str(body.get("priority", "4")),
        "category": body.get("category", "inquiry"),
        "correlation_id": body.get("correlation_id", ""),
        "caller_id": body.get("caller_id", info["user"]),
        "assignment_group": body.get("assignment_group", "Service Desk"),
        "sys_created_by": info["user"],
    }
    incidents[sys_id] = row
    response.status_code = 201
    return {"result": row}
