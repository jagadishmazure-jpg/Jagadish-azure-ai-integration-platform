"""Listen trigger.

A subscribed work event starts one context-agent run. The run sanitizes the event text first
(no tools), then grounds as the user named on the event. The raw text is not stored. This is
the offline stand-in for the Work IQ Listen capability: an event, not a poll, starts the run.
"""

from __future__ import annotations

from dataclasses import dataclass

from aiip.retrieval.access import ground
from aiip.retrieval.corpus import DIRECTORY
from aiip.shared import errors as E
from aiip.shared.audit import digest
from aiip.shared.auth import Principal, validate_token
from aiip.shared.untrusted import WITHHELD, screen

EVENT_TYPE = "com.contoso.work.message.received.v1"
_runs: list[dict] = []
_seq = 0


@dataclass(frozen=True)
class Subscription:
    id: str
    event_type: str
    agent_id: str


SUBSCRIPTIONS: tuple[Subscription, ...] = (Subscription("sub-work-message", EVENT_TYPE, "context-agent"),)


def reset_runs() -> None:
    global _seq
    _runs.clear()
    _seq = 0


def runs() -> list[dict]:
    return [dict(r) for r in _runs]


async def _listen_principal() -> Principal:
    from aiip.identity.client import IdentityClient
    from aiip.identity.registrations import CONTEXT_GW

    token = await IdentityClient("context-listen").agent_token(CONTEXT_GW)
    return await validate_token(token, CONTEXT_GW)


async def start_run(event: dict) -> dict | None:
    """Start an agent run when a subscription matches. Unknown types start nothing."""
    matched = next((s for s in SUBSCRIPTIONS if s.event_type == event.get("type")), None)
    if matched is None:
        return None
    global _seq
    _seq += 1
    data = event.get("data") or {}
    raw = str(data.get("text", ""))
    user = str(data.get("user", ""))
    record = {
        "run_id": f"run-{_seq:04d}",
        "agent_id": matched.agent_id,
        "event_id": str(event.get("id", "")),
        "event_type": matched.event_type,
        "user": user if user in DIRECTORY else "",
        "outcome": "fail_closed",
        "doc_ids": [],
        "steps": ["sanitize"],
        "text_digest": digest(raw),
        "mode": "",
    }
    cleaned, flags = screen(raw)
    if flags or cleaned == WITHHELD or not isinstance(cleaned, str) or not cleaned.strip():
        record["outcome"] = "withheld"
        _runs.append(record)
        return dict(record)
    if user not in DIRECTORY:
        _runs.append(record)
        return dict(record)
    principal = await _listen_principal()
    try:
        grounded = ground(principal, cleaned, mode="plan", trusted_user=user)
    except E.GatewayError:
        record["outcome"] = "denied"
        _runs.append(record)
        return dict(record)
    record["steps"] = ["sanitize", "plan", "ground"]
    record["outcome"] = "grounded"
    record["doc_ids"] = list(grounded["doc_ids"])
    record["mode"] = grounded["mode"]
    _runs.append(record)
    return dict(record)
