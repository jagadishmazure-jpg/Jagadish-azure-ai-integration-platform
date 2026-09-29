"""Salesforce REST API stand-in (v62.0 shapes): OAuth 2.0 JWT bearer flow, sObject reads with
record-level sharing, SOQL-lite query, upsert by external id, Sforce-Limit-Info header."""

from __future__ import annotations

import re

import jwt
from fastapi import APIRouter, Form, Header, Response
from fastapi.responses import JSONResponse

from aiip.fakesaas import state
from aiip.shared.secrets import RESOLVER

router = APIRouter(prefix="/salesforce")
API = "/services/data/v62.0"
CONNECTED_APP_KEY_REF = "kv://aiip-local-kv/salesforce-connected-app-key"


def _err(status: int, code: str, msg: str) -> JSONResponse:
    return JSONResponse([{"errorCode": code, "message": msg}], status)


def _limits(resp: Response) -> None:
    used = state.CALLS.get("salesforce", 0)
    resp.headers["Sforce-Limit-Info"] = f"api-usage={used}/15000"


def _user(authorization: str | None) -> dict:
    info = state.token_info("salesforce", authorization)
    return {
        "username": info["user"],
        **state.DATA["salesforce"]["users"].get(info["user"], {"region": "", "view_all": False}),
    }


def _can_see(user: dict, account: dict) -> bool:
    return user.get("view_all") or user.get("region") == account.get("Region__c")


@router.post("/services/oauth2/token")
async def token(grant_type: str = Form(...), assertion: str = Form(...)):
    await state.apply_fault("salesforce")
    if grant_type != "urn:ietf:params:oauth:grant-type:jwt-bearer":
        return JSONResponse({"error": "unsupported_grant_type"}, 400)
    try:
        claims = jwt.decode(
            assertion,
            RESOLVER.resolve(CONNECTED_APP_KEY_REF),
            algorithms=["HS256"],
            audience="https://login.salesforce.com",
        )
    except jwt.PyJWTError:
        return JSONResponse({"error": "invalid_grant", "error_description": "invalid assertion"}, 400)
    if claims.get("sub") not in state.DATA["salesforce"]["users"]:
        return JSONResponse(
            {"error": "invalid_grant", "error_description": "user hasn't approved this consumer"}, 400
        )
    return {
        "access_token": state.issue_token("salesforce", claims["sub"]),
        "instance_url": "http://fake-saas/salesforce",
        "token_type": "Bearer",
        "scope": "api",
    }


@router.get(API + "/sobjects/Account/{account_id}")
async def get_account(account_id: str, response: Response, authorization: str | None = Header(default=None)):
    await state.apply_fault("salesforce")
    user = _user(authorization)
    acct = state.DATA["salesforce"]["accounts"].get(account_id)
    _limits(response)
    if acct is None:
        return _err(404, "NOT_FOUND", "The requested resource does not exist")
    if not _can_see(user, acct):
        return _err(403, "INSUFFICIENT_ACCESS", "insufficient access rights on object id")
    return {"attributes": {"type": "Account", "url": f"{API}/sobjects/Account/{account_id}"}, **acct}


@router.get(API + "/query")
async def query(q: str, response: Response, authorization: str | None = Header(default=None)):
    await state.apply_fault("salesforce")
    user = _user(authorization)
    _limits(response)
    m = re.search(r"FROM\s+(\w+)\s+WHERE\s+(\w+)\s*=\s*'([^']+)'", q, re.I)
    if not m:
        return _err(400, "MALFORMED_QUERY", "unsupported query in sandbox")
    sobject, field, value = m.groups()
    accounts = state.DATA["salesforce"]["accounts"]
    if sobject.lower() == "account":
        rows = [a for a in accounts.values() if str(a.get(field)) == value and _can_see(user, a)]
    elif sobject.lower() == "case":
        rows = [
            c
            for c in state.DATA["salesforce"]["cases"].values()
            if str(c.get(field)) == value and _can_see(user, accounts.get(c["AccountId"], {}))
        ]
    else:
        return _err(400, "INVALID_TYPE", f"sObject type '{sobject}' is not supported")
    return {
        "totalSize": len(rows),
        "done": True,
        "records": [{"attributes": {"type": sobject}, **r} for r in rows],
    }


@router.patch(API + "/sobjects/Case/External_Id__c/{external_id}")
async def upsert_case(
    external_id: str, body: dict, response: Response, authorization: str | None = Header(default=None)
):
    await state.apply_fault("salesforce")
    user = _user(authorization)
    _limits(response)
    acct = state.DATA["salesforce"]["accounts"].get(body.get("AccountId", ""))
    if acct is None:
        return _err(400, "INVALID_CROSS_REFERENCE_KEY", "invalid cross reference id")
    if not _can_see(user, acct):
        return _err(
            403,
            "INSUFFICIENT_ACCESS_ON_CROSS_REFERENCE_ENTITY",
            "insufficient access rights on cross-reference id",
        )
    if not body.get("Subject"):
        return _err(400, "REQUIRED_FIELD_MISSING", "Required fields are missing: [Subject]")
    cases = state.DATA["salesforce"]["cases"]
    existing = next((c for c in cases.values() if c.get("External_Id__c") == external_id), None)
    if existing:
        existing.update(
            {k: v for k, v in body.items() if k in {"Subject", "Priority", "Status", "Description"}}
        )
        response.status_code = 200
        return {"id": existing["Id"], "success": True, "errors": [], "created": False}
    case_id = f"500A{len(cases) + 1:06d}"
    cases[case_id] = {
        "Id": case_id,
        "CaseNumber": f"{1000 + len(cases) + 1:08d}",
        "AccountId": body["AccountId"],
        "Subject": body["Subject"],
        "Status": body.get("Status", "New"),
        "Priority": body.get("Priority", "Medium"),
        "External_Id__c": external_id,
        "CreatedBy": user["username"],
    }
    response.status_code = 201
    return {"id": case_id, "success": True, "errors": [], "created": True}
