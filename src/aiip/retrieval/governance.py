"""Admin ceiling and per-agent policy for the work-context path.

The ceiling is the tenant upper bound (for example read, create and update, but not delete).
An agent policy may narrow that set and may name the data domains the agent may touch. It
cannot add a verb the ceiling does not allow. WorkIQ.Read and WorkIQ.Act are separate grants
on the context gateway. A verb in ``ask_on`` stops for a person instead of running.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiip.identity.registrations import CONTEXT_GW, REGISTRATIONS
from aiip.shared import errors as E
from aiip.shared.audit import digest
from aiip.shared.auth import Principal

VERBS = frozenset({"read", "create", "update", "delete"})
READ_GRANT = "WorkIQ.Read"
ACT_GRANT = "WorkIQ.Act"
DEFAULT_CEILING = frozenset({"read", "create", "update"})


@dataclass(frozen=True)
class AgentPolicy:
    agent: str
    verbs: frozenset[str]
    domains: frozenset[str]
    ask_on: frozenset[str]


@dataclass(frozen=True)
class Decision:
    effect: str  # allow | block | ask
    code: str
    reason: str


def _policy(
    agent: str,
    verbs: set[str] | frozenset[str],
    domains: set[str] | frozenset[str],
    ask: set[str] | frozenset[str] = frozenset(),
) -> AgentPolicy:
    return AgentPolicy(agent, frozenset(verbs), frozenset(domains), frozenset(ask))


_DEFAULTS: dict[str, AgentPolicy] = {
    p.agent: p
    for p in (
        _policy(
            "care-planner",
            {"read", "create", "update"},
            {"care", "sales", "procurement", "compliance"},
            {"update"},
        ),
        _policy("sales-agent", {"read"}, {"sales"}),
        _policy("hr-agent", {"read"}, {"hr"}),
        _policy("context-listen", {"read"}, {"care", "sales", "procurement", "compliance"}),
    )
}

_ceiling: set[str] = set(DEFAULT_CEILING)
_policies: dict[str, AgentPolicy] = dict(_DEFAULTS)
_pending: dict[str, AgentPolicy] = {}
_items: dict[str, dict[str, str]] = {}
_audit: list[dict[str, str]] = []


def reset_state() -> None:
    global _ceiling
    _ceiling = set(DEFAULT_CEILING)
    _policies.clear()
    _policies.update(_DEFAULTS)
    _pending.clear()
    _items.clear()
    _audit.clear()


def ceiling() -> frozenset[str]:
    return frozenset(_ceiling)


def set_ceiling(verbs: list[str] | set[str]) -> frozenset[str]:
    unknown = set(verbs) - VERBS
    if unknown or not verbs:
        raise E.GatewayError(
            E.VALIDATION, "ceiling verbs must be a non-empty subset of read, create, update, delete"
        )
    _ceiling.clear()
    _ceiling.update(verbs)
    return ceiling()


def policy_for(agent: str) -> AgentPolicy | None:
    return _policies.get(agent)


def propose(agent: str, verbs: list[str], domains: list[str], ask_on: list[str] | None = None) -> None:
    """Maker proposal. Rejected immediately when it is wider than the current ceiling."""
    requested = set(verbs)
    if not requested or not requested <= _ceiling or not requested <= VERBS:
        raise E.GatewayError(E.AUTHZ_DENY, "agent policy cannot exceed the admin ceiling")
    ask = set(ask_on or [])
    if not ask <= requested:
        raise E.GatewayError(E.VALIDATION, "ask_on must be verbs the policy already allows")
    _pending[agent] = _policy(agent, requested, set(domains), ask)


def approve(agent: str) -> AgentPolicy:
    pending = _pending.pop(agent, None)
    if pending is None:
        raise E.GatewayError(E.NOT_FOUND, "no pending policy")
    if not pending.verbs <= _ceiling:
        raise E.GatewayError(E.AUTHZ_DENY, "agent policy cannot exceed the admin ceiling")
    _policies[agent] = pending
    return pending


def grants(actor: str) -> set[str]:
    reg = REGISTRATIONS.get(actor)
    if reg is None:
        return set()
    return set(reg.app_roles.get(CONTEXT_GW, ()))


def authorize(principal: Principal, verb: str) -> Decision:
    if verb not in VERBS:
        return Decision("block", E.VALIDATION, "unknown verb")
    if verb not in _ceiling:
        return Decision("block", E.AUTHZ_DENY, "above the admin ceiling")
    policy = _policies.get(principal.actor)
    if policy is None:
        return Decision("block", E.AUTHZ_DENY, f"no work policy for {principal.actor}")
    if verb not in policy.verbs:
        return Decision("block", E.AUTHZ_DENY, "outside this agent's policy")
    held = grants(principal.actor) | set(principal.roles) | set(principal.scopes)
    need = READ_GRANT if verb == "read" else ACT_GRANT
    if need not in held:
        return Decision("block", E.AUTHZ_DENY, f"missing {need}")
    if verb in policy.ask_on:
        return Decision("ask", E.APPROVAL_REQUIRED, "a person must confirm this action")
    return Decision("allow", E.OK, "within ceiling and agent policy")


def require(principal: Principal, verb: str) -> AgentPolicy:
    decision = authorize(principal, verb)
    if decision.effect != "allow":
        raise E.GatewayError(decision.code, decision.reason)
    policy = _policies[principal.actor]
    return policy


def domain_allowed(principal: Principal, domain: str) -> None:
    policy = _policies.get(principal.actor)
    if policy is None or domain not in policy.domains:
        raise E.GatewayError(E.AUTHZ_DENY, "outside this agent's data domains")


def record(principal: Principal, operation: str, result: str, business_key: str) -> None:
    """Audit the decision. The digest covers ids only, never passage text."""
    _audit.append(
        {
            "actor": principal.actor,
            "subject": principal.subject,
            "operation": operation,
            "result": result,
            "business_key": business_key,
            "args_digest": digest({"operation": operation, "business_key": business_key}),
        }
    )


def audit_records() -> list[dict[str, str]]:
    return list(_audit)


def put_item(resource: str, body: str, owner: str, domain: str) -> dict[str, str]:
    _items[resource] = {"resource": resource, "body": body, "owner": owner, "domain": domain}
    return dict(_items[resource])


def get_item(resource: str) -> dict[str, str] | None:
    item = _items.get(resource)
    return dict(item) if item else None


def items() -> dict[str, dict[str, str]]:
    return {k: dict(v) for k, v in _items.items()}


def public_policy(agent: str) -> dict:
    policy = _policies.get(agent)
    return {
        "ceiling": sorted(_ceiling),
        "agent": None
        if policy is None
        else {
            "agent": policy.agent,
            "verbs": sorted(policy.verbs),
            "domains": sorted(policy.domains),
            "ask_on": sorted(policy.ask_on),
        },
        "pending": agent in _pending,
    }
