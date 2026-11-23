"""REST surface for the governed work-context path. MCP and A2A call the same functions."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from pydantic import BaseModel, ConfigDict, Field

from aiip.identity.registrations import CONTEXT_GW
from aiip.retrieval import governance, listen
from aiip.retrieval.access import act, ground
from aiip.shared import errors as E
from aiip.shared.auth import Principal, principal_dependency

app = FastAPI(title="Work context", version="1.0.0")
E.install_error_handlers(app)
auth = principal_dependency(CONTEXT_GW)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GroundBody(_Strict):
    question: str = Field(min_length=1, max_length=500)
    mode: str = "plan"


class ActBody(_Strict):
    verb: str
    resource: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9-]{1,40}$")
    body: str = Field(default="", max_length=500)
    domain: str = "sales"


class CeilingBody(_Strict):
    verbs: list[str] = Field(min_length=1)


class PolicyBody(_Strict):
    verbs: list[str]
    domains: list[str]
    ask_on: list[str] = []


class ListenBody(_Strict):
    id: str = Field(min_length=1, max_length=80)
    type: str
    data: dict


def _admin(principal: Principal) -> None:
    if "OpsApprover" not in principal.roles and "Context.Admin" not in principal.roles:
        raise E.GatewayError(E.AUTHZ_DENY, "ceiling changes require an admin role")


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "service": "context-gateway"}


@app.get("/v1/policy")
async def policy(p: Principal = Depends(auth)) -> dict:
    return governance.public_policy(p.actor)


@app.post("/v1/ground")
async def ground_route(body: GroundBody, p: Principal = Depends(auth)) -> dict:
    return ground(p, body.question, body.mode)


@app.post("/v1/act")
async def act_route(body: ActBody, p: Principal = Depends(auth)) -> dict:
    return act(p, body.verb, body.resource, body.body, body.domain)


@app.put("/v1/admin/ceiling")
async def put_ceiling(body: CeilingBody, p: Principal = Depends(auth)) -> dict:
    _admin(p)
    return {"verbs": sorted(governance.set_ceiling(body.verbs))}


@app.post("/v1/admin/policies/{agent}")
async def propose_policy(agent: str, body: PolicyBody, p: Principal = Depends(auth)) -> dict:
    _admin(p)
    governance.propose(agent, body.verbs, body.domains, body.ask_on)
    return {"pending": agent}


@app.post("/v1/admin/policies/{agent}/approve")
async def approve_policy(agent: str, p: Principal = Depends(auth)) -> dict:
    _admin(p)
    approved = governance.approve(agent)
    return {"agent": approved.agent, "verbs": sorted(approved.verbs)}


@app.post("/v1/listen/events")
async def post_event(body: ListenBody, p: Principal = Depends(auth)) -> dict:
    if p.actor != "context-listen":
        raise E.GatewayError(E.AUTHZ_DENY, "only the listen worker may start a run from an event")
    run = await listen.start_run({"id": body.id, "type": body.type, "data": body.data})
    return {"started": run is not None, "run": run}
