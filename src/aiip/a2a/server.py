"""A2A server factory (a2a-sdk 1.1). Each inbound task re-validates the bearer token (aud = this
agent), the caller allow-list and the skill input schema: defense in depth behind the A2A gateway.
Skill handlers receive a SkillContext with the principal, raw token, traceparent and hop count."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from a2a.helpers.proto_helpers import get_data_parts, new_data_message
from a2a.server.agent_execution import AgentExecutor
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.routes.fastapi_routes import add_a2a_routes_to_fastapi
from a2a.server.tasks import InMemoryTaskStore
from fastapi import FastAPI

from aiip.a2a.cards import card_json, to_agent_card
from aiip.a2a.specs import AgentSpec
from aiip.identity.registrations import agent_uri
from aiip.shared import errors as E
from aiip.shared.auth import Principal, validate_token
from aiip.shared.schema import errors as schema_errors


@dataclass
class SkillContext:
    principal: Principal
    token: str
    traceparent: str
    hops: int
    tenant_id: str


# a2a-sdk 1.1.5 logs a benign warning at queue close when the agent replies with a single message
logging.getLogger("a2a.server.events.event_queue_v2").setLevel(logging.ERROR)

SkillHandler = Callable[[str, dict[str, Any], SkillContext], Awaitable[dict[str, Any]]]


class IntegrationAgentExecutor(AgentExecutor):
    def __init__(self, spec: AgentSpec, handler: SkillHandler) -> None:
        self.spec, self.handler = spec, handler

    async def execute(self, context, event_queue) -> None:
        state = context.call_context.state if context.call_context else {}
        headers = {k.lower(): v for k, v in state.get("headers", {}).items()}

        async def reply(payload: dict[str, Any]) -> None:
            await event_queue.enqueue_event(
                new_data_message(payload, context_id=context.context_id, task_id=context.task_id)
            )

        auth = headers.get("authorization", "")
        try:
            if not auth.lower().startswith("bearer "):
                raise E.GatewayError(E.AUTHN_FAILED, "bearer token required")
            token = auth.split(" ", 1)[1]
            p = await validate_token(token, agent_uri(self.spec.id))
        except E.GatewayError as exc:
            await reply({"error": exc.code, "detail": exc.message})
            return
        if p.actor not in self.spec.allowed_callers:
            await reply({"error": E.AUTHZ_DENY, "detail": f"{p.actor} may not call {self.spec.id}"})
            return
        if not p.is_user and "Agent.Invoke" not in p.roles:
            await reply({"error": E.AUTHZ_DENY, "detail": "app-only caller lacks Agent.Invoke"})
            return
        if headers.get("x-tenant-id") and headers["x-tenant-id"] != p.tenant_id:
            await reply({"error": E.AUTHZ_DENY, "detail": "tenant header does not match token"})
            return
        parts = get_data_parts(context.message.parts) if context.message else []
        body = parts[0] if parts and isinstance(parts[0], dict) else {}
        skill_id, args = body.get("skill", ""), body.get("input", {}) or {}
        skill = self.spec.skill(skill_id)
        if skill is None:
            await reply({"error": E.NOT_FOUND, "detail": f"unknown skill {skill_id}"})
            return
        if errs := schema_errors(skill.input_schema, args):
            await reply({"error": E.VALIDATION, "detail": errs})
            return
        ctx = SkillContext(
            p, token, headers.get("traceparent", ""), int(headers.get("x-a2a-hops", "0") or 0), p.tenant_id
        )
        try:
            output = await self.handler(skill_id, args, ctx)
        except E.GatewayError as exc:
            await reply({"error": exc.code, "detail": exc.message, "skill": skill_id})
            return
        except Exception as exc:  # typed failure back to the caller; the trace stays in our logs
            await reply({"error": E.INTERNAL, "detail": type(exc).__name__, "skill": skill_id})
            return
        await reply({"skill": skill_id, "agent": self.spec.key, "output": output})

    async def cancel(self, context, event_queue) -> None:
        return None


def mount(
    app: FastAPI, spec: AgentSpec, handler: SkillHandler, card_path: str = "/.well-known/agent-card.json"
) -> None:
    card = to_agent_card(spec)
    rh = DefaultRequestHandler(
        agent_executor=IntegrationAgentExecutor(spec, handler),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card, card_url=card_path),
        jsonrpc_routes=create_jsonrpc_routes(rh, rpc_url=spec.rpc_path),
    )


def create_agent_app(specs: list[AgentSpec], handler: SkillHandler) -> FastAPI:
    """One process can serve several published versions (e.g. crm-agent v1 at /a2a, v2 at /v2/a2a)."""
    latest = specs[-1]
    app = FastAPI(title=latest.name, version=latest.version)
    for spec in specs:
        path = (
            "/.well-known/agent-card.json"
            if spec.rpc_path == "/a2a"
            else spec.rpc_path.rsplit("/", 1)[0] + "/.well-known/agent-card.json"
        )
        mount(app, spec, handler, path)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True, "agent": latest.id, "versions": [s.version for s in specs]}

    @app.get("/card")
    async def card_view() -> dict:
        return {s.version: card_json(s) for s in specs}

    return app
