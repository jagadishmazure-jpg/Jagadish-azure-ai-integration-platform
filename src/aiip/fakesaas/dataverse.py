"""Dataverse Web API stand-in (`/api/data/v9.2`): accepts Entra access tokens for the environment
URL (so OBO tokens flow straight through), applies business-unit style row security by region,
alternate-key upsert with PATCH, and x-ms-ratelimit-* headers."""

from __future__ import annotations

import re

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse

from aiip.config import USERS_BY_OID
from aiip.fakesaas import state
from aiip.identity.registrations import DATAVERSE
from aiip.shared import errors as E
from aiip.shared.auth import validate_token

router = APIRouter(prefix="/dataverse")
API = "/api/data/v9.2"


async def _principal(authorization: str | None):
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    try:
        return await validate_token(authorization.split(" ", 1)[1], DATAVERSE)
    except E.GatewayError:
        return None


def _limits(resp: Response) -> None:
    resp.headers["x-ms-ratelimit-burst-remaining-xrm-requests"] = str(
        max(0, 5999 - state.CALLS.get("dataverse", 0))
    )


def _err(status: int, code: str, msg: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": msg}}, status)


@router.get(API + "/entitlements")
async def entitlements(
    request: Request, response: Response, authorization: str | None = Header(default=None)
):
    await state.apply_fault("dataverse")
    p = await _principal(authorization)
    if p is None:
        return _err(401, "0x80072560", "The user is not a member of the organization.")
    _limits(response)
    flt = request.query_params.get("$filter", "")
    m = re.search(r"_customerid_value eq '([^']+)'", flt)
    rows = state.DATA["dataverse"]["entitlements"]
    if m:
        rows = [r for r in rows if r["_customerid_value"] == m.group(1)]
    user = USERS_BY_OID.get(p.subject_id)
    if p.is_user:  # row-level security: users see their business unit's customers only
        region = user.region if user else p.claims.get("region", "")
        rows = [r for r in rows if r["region"] == region]
    return {"@odata.context": f"{API}/$metadata#entitlements", "value": rows}


@router.patch(API + "/incidents(ticketnumber='{ticket}')")
async def upsert_incident(
    ticket: str, request: Request, response: Response, authorization: str | None = Header(default=None)
):
    await state.apply_fault("dataverse")
    p = await _principal(authorization)
    if p is None:
        return _err(401, "0x80072560", "The user is not a member of the organization.")
    _limits(response)
    body = await request.json()
    store = state.DATA["dataverse"]["incidents"]
    created = ticket not in store
    store[ticket] = {**store.get(ticket, {}), **body, "ticketnumber": ticket, "modifiedby": p.subject}
    response.headers["OData-EntityId"] = f"{API}/incidents(ticketnumber='{ticket}')"
    response.status_code = 204
    response.headers["x-aiip-created"] = "true" if created else "false"
    return None
