"""MCP tools for the same governed path. The caller is the bearer token, not a tool argument."""

from __future__ import annotations

from contextvars import ContextVar

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from aiip.retrieval.access import act, ground
from aiip.shared import errors as E
from aiip.shared.auth import Principal

server = MCPServer(
    "work-context",
    instructions="Permission-trimmed grounding and governed actions. Sandbox stand-in, not Work IQ.",
)
READ = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, idempotentHint=False, openWorldHint=False)

_CALLER: ContextVar[Principal | None] = ContextVar("work_context_caller", default=None)
# In-process MCP clients may hop tasks. The bound principal is the caller's, set for that call.
_bound: Principal | None = None


def bind(principal: Principal):
    global _bound
    _bound = principal
    return _CALLER.set(principal)


def reset(token) -> None:
    global _bound
    _bound = None
    _CALLER.reset(token)


def _caller() -> Principal:
    principal = _CALLER.get() or _bound
    if principal is None:
        raise E.GatewayError(E.AUTHN_FAILED, "caller required")
    return principal


class GroundOut(BaseModel):
    ok: bool = True
    code: str = ""
    message: str = ""
    mode: str = ""
    doc_ids: list[str] = Field(default_factory=list)
    hits: list[dict] = Field(default_factory=list)
    topics: list[dict] = Field(default_factory=list)
    note: str = ""


class ActOut(BaseModel):
    ok: bool = True
    code: str = ""
    message: str = ""
    verb: str = ""
    resource: str = ""


@server.tool(annotations=READ)
async def ground_question(question: str, mode: str = "plan") -> GroundOut:
    """Ground a question on passages the caller may read."""
    try:
        result = ground(_caller(), question, mode)
    except E.GatewayError as exc:
        return GroundOut(ok=False, code=exc.code, message=exc.message)
    return GroundOut(
        mode=result["mode"],
        doc_ids=result["doc_ids"],
        hits=result["hits"],
        topics=result["topics"],
        note=result["note"],
    )


@server.tool(annotations=WRITE)
async def act_on(verb: str, resource: str, body: str = "", domain: str = "sales") -> ActOut:
    """Create or update a work item inside the admin ceiling."""
    try:
        result = act(_caller(), verb, resource, body, domain)
    except E.GatewayError as exc:
        return ActOut(ok=False, code=exc.code, message=exc.message, verb=verb, resource=resource)
    return ActOut(ok=True, verb=verb, resource=result["item"]["resource"])
