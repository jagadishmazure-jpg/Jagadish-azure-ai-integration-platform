"""Agent directory: version resolution, caller allow-lists, hop cap."""

from __future__ import annotations

import re

from aiip.a2a.specs import AGENTS, AgentSpec
from aiip.shared import errors as E

MAX_HOPS = 3


def _v(spec: AgentSpec) -> tuple[int, ...]:
    return tuple(int(x) for x in spec.version.split("."))


def resolve(agent_id: str, requested: str | None) -> AgentSpec:
    """`requested` may be empty (latest), "2" / "^2" (latest in major 2) or an exact "1.3.0"."""
    candidates = sorted((a for a in AGENTS if a.id == agent_id and a.kind == "a2a-agent"), key=_v)
    if not candidates:
        raise E.GatewayError(E.NOT_FOUND, f"agent {agent_id} is not in the directory")
    if not requested:
        return candidates[-1]
    req = requested.strip().lstrip("^")
    if re.fullmatch(r"\d+", req):
        in_major = [a for a in candidates if a.version.split(".")[0] == req]
        if in_major:
            return in_major[-1]
    exact = [a for a in candidates if a.version == req]
    if exact:
        return exact[0]
    raise E.GatewayError(E.NOT_FOUND, f"{agent_id} has no version matching {requested}")


def authorize_caller(caller: str, callee: AgentSpec) -> None:
    if caller not in callee.allowed_callers:
        raise E.GatewayError(E.AUTHZ_DENY, f"{caller} is not on {callee.id}'s caller allow-list")


def next_hop(current: str | None) -> int:
    try:
        hops = int(current or 0)
    except ValueError:
        hops = 0
    if hops + 1 > MAX_HOPS:
        raise E.GatewayError(E.AUTHZ_DENY, f"hop cap exceeded ({MAX_HOPS})")
    return hops + 1


def listing() -> list[dict]:
    from aiip.a2a.cards import contract

    return [
        {
            "id": a.id,
            "version": a.version,
            "kind": a.kind,
            "name": a.name,
            **{k: v for k, v in contract(a).items() if k not in {"agent_id", "version"}},
        }
        for a in AGENTS
    ]
