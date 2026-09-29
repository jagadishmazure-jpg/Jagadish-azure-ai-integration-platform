"""Jira Cloud REST v3 stand-in: basic auth with an API token, issue create, JQL search on the
enhanced `/search/jql` endpoint, X-RateLimit-* and Retry-After headers."""

from __future__ import annotations

import base64
import re

from fastapi import APIRouter, Header, Request, Response
from fastapi.responses import JSONResponse

from aiip.fakesaas import state
from aiip.shared.secrets import RESOLVER

router = APIRouter(prefix="/jira")
API_TOKEN_REF = "kv://aiip-local-kv/jira-api-token"
SERVICE_ACCOUNT = "aiip-integration@contoso.example"


def _check(authorization: str | None) -> bool:
    if not authorization or not authorization.lower().startswith("basic "):
        return False
    try:
        user, token = base64.b64decode(authorization.split(" ", 1)[1]).decode().split(":", 1)
    except Exception:
        return False
    return user == SERVICE_ACCOUNT and token == RESOLVER.resolve(API_TOKEN_REF)


def _limits(resp: Response) -> None:
    resp.headers["X-RateLimit-Limit"] = "350"
    resp.headers["X-RateLimit-Remaining"] = str(max(0, 350 - state.CALLS.get("jira", 0)))


@router.get("/rest/api/3/search/jql")
async def search(jql: str, response: Response, authorization: str | None = Header(default=None)):
    await state.apply_fault("jira")
    if not _check(authorization):
        return JSONResponse({"errorMessages": ["Client must be authenticated to access this resource."]}, 401)
    _limits(response)
    m = re.search(r'labels\s*=\s*"?([\w\-:.]+)"?', jql)
    issues = [
        i for i in state.DATA["jira"]["issues"].values() if not m or m.group(1) in i["fields"]["labels"]
    ]
    return {"issues": issues, "isLast": True}


@router.post("/rest/api/3/issue")
async def create(request: Request, response: Response, authorization: str | None = Header(default=None)):
    await state.apply_fault("jira")
    if not _check(authorization):
        return JSONResponse({"errorMessages": ["Client must be authenticated to access this resource."]}, 401)
    _limits(response)
    body = await request.json()
    f = body.get("fields", {})
    project = f.get("project", {}).get("key")
    if project not in state.DATA["jira"]["projects"]:
        return JSONResponse({"errorMessages": [], "errors": {"project": "valid project is required"}}, 400)
    issues = state.DATA["jira"]["issues"]
    key = f"{project}-{len(issues) + 1}"
    issues[key] = {"id": str(10000 + len(issues)), "key": key, "fields": {**f, "labels": f.get("labels", [])}}
    response.status_code = 201
    return {"id": issues[key]["id"], "key": key, "self": f"/jira/rest/api/3/issue/{issues[key]['id']}"}
