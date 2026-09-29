"""Microsoft Agent Framework plumbing.

Offline, agents run on `PlanFollowingChatClient`: a real MAF `BaseChatClient` (with the function
invocation layer) that executes the tool plan handed to it and then returns the tool results. So
tool calling, middleware and telemetry go through MAF exactly as with a hosted model, but runs are
deterministic. With AIIP_MODE=azure the same agents use `FoundryChatClient` (gpt-5-mini by default)."""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

from agent_framework import Agent, BaseChatClient, ChatResponse, Content, Message
from agent_framework._tools import FunctionInvocationLayer

from aiip.config import is_azure


class PlanFollowingChatClient(FunctionInvocationLayer, BaseChatClient):
    OTEL_PROVIDER_NAME = "aiip-deterministic"

    async def _inner_get_response(self, *, messages, stream, options, **kwargs):  # type: ignore[override]
        msgs = list(messages)
        results = [c for m in msgs for c in m.contents if c.type == "function_result"]
        user = next((m.text for m in reversed(msgs) if m.role == "user"), "")
        try:
            plan = json.loads(user)
        except json.JSONDecodeError:
            plan = {}
        calls = plan.get("calls", [])
        if calls and not results:
            contents = [
                Content.from_function_call(uuid.uuid4().hex[:8], c["tool"], arguments=c.get("args", {}))
                for c in calls
            ]
            return ChatResponse(
                messages=[Message(role="assistant", contents=contents)], model="deterministic"
            )
        text = json.dumps({"results": [str(r.result) for r in results], "note": plan.get("compose", "")})
        return ChatResponse(messages=[Message(role="assistant", contents=[text])], model="deterministic")


def chat_client() -> BaseChatClient:
    if not is_azure():
        return PlanFollowingChatClient()
    from agent_framework_foundry import FoundryChatClient  # pragma: no cover
    from azure.identity import DefaultAzureCredential

    return FoundryChatClient(
        project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
        model=os.environ.get("FOUNDRY_MODEL", "gpt-5-mini"),
        credential=DefaultAzureCredential(),
    )


async def run_agent(
    name: str, instructions: str, tools: list[Any], calls: list[dict[str, Any]], compose: str = ""
) -> str:
    """Run one MAF agent turn. `calls` is the deterministic plan (ignored by a hosted model, which
    chooses tools from `instructions` and the tool schemas)."""
    agent = Agent(client=chat_client(), instructions=instructions, name=name, tools=tools)
    resp = await agent.run(json.dumps({"calls": calls, "compose": compose}))
    return resp.text
