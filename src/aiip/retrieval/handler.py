"""A2A skills for the context agent. Same ``ground`` and ``act`` functions as REST and MCP."""

from __future__ import annotations

from typing import Any

from aiip.a2a.server import SkillContext
from aiip.retrieval.access import act, ground


async def context_handler(skill_id: str, args: dict[str, Any], ctx: SkillContext) -> dict[str, Any]:
    if skill_id == "ground":
        return ground(ctx.principal, args["question"], args.get("mode", "plan"))
    if skill_id == "act":
        return act(
            ctx.principal,
            args["verb"],
            args["resource"],
            args.get("body", ""),
            args.get("domain", "sales"),
        )
    return {"error": "not_found", "detail": skill_id}
