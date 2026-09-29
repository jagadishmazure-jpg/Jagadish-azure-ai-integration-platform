"""Activities for the vendor-invoice process. Each is a plain async function (the Durable Functions
app and the local runtime call the same code). Activities run under the orchestrator's own agent
identity; the human approver is the *subject* recorded on the gated commit.

Business outcomes (business_reject, authz_deny...) are returned as data, not raised, so the
orchestrator can branch and compensate deterministically."""

from __future__ import annotations

import uuid
from typing import Any

from aiip.a2a import client as a2a
from aiip.agents.gateways import call_tool
from aiip.config import TENANTS
from aiip.identity.client import IdentityClient
from aiip.identity.registrations import TOOL_GW, agent_uri
from aiip.shared import errors as E
from aiip.shared import http, telemetry

IDENTITY = IdentityClient("bpm-invoice-orchestrator")
PROCESS_MAP: dict[str, dict[str, Any]] = {}  # process id -> graph runs (Cosmos DB / Table storage in Azure)


async def register_run(p: dict) -> dict:
    run_id = "graph-run-" + uuid.uuid4().hex[:12]
    PROCESS_MAP.setdefault(p["process_id"], {"process": p["process"], "runs": [], "outcome": None})[
        "runs"
    ].append(run_id)
    return {"run_id": run_id}


async def _agent(skill: str, payload: dict, p: dict) -> dict:
    token = await IDENTITY.agent_token(agent_uri("ap-invoice-agent"), p["tenant"])
    try:
        out = await a2a.send("ap-invoice-agent", skill, payload, token=token, tenant_id=TENANTS[p["tenant"]])
    except E.GatewayError as exc:
        return {"ok": False, "result_class": exc.code, "message": exc.message}
    return {"ok": True, "result": out["output"], "graph_run_id": p["run_id"]}


async def agent_extract(p: dict) -> dict:
    return await _agent("extract", {"text": p["text"]}, p)


async def agent_classify(p: dict) -> dict:
    return await _agent("classify", {"invoice": p["invoice"]}, p)


async def agent_draft(p: dict) -> dict:
    return await _agent("draft", {"kind": p["kind"], "facts": p["facts"]}, p)


async def sap_commit(p: dict) -> dict:
    """Commit through the Tool Gateway. Idempotency key = process id + step + tool, so a Durable
    activity retry (or replay after a host crash) can never double-post."""
    token = await IDENTITY.agent_token(TOOL_GW, p["tenant"])
    key = f"{p['process_id']}:{p['step']}:{p['tool']}"
    try:
        res = await call_tool(
            p["tool"], p["args"], token=token, idempotency_key=key, approval_id=p.get("approval_id")
        )
    except E.GatewayError as exc:
        return {"ok": False, "result_class": exc.code, "message": exc.message, "tool": p["tool"]}
    return {
        "ok": True,
        "data": res["data"],
        "replayed": res.get("replayed", False),
        "approved_by": res.get("approved_by"),
    }


async def find_approver(p: dict) -> dict:
    token = await IDENTITY.agent_token(TOOL_GW, p["tenant"])
    try:
        res = await call_tool(
            "hr.find_approver", {"cost_center": p["cost_center"], "amount": p["amount"]}, token=token
        )
    except E.GatewayError as exc:
        return {"approver": None, "result_class": exc.code}
    return {"approver": res["data"]["approver"]}


async def request_approval(p: dict) -> dict:
    """Creates the approval record the human will decide on, and drafts the approver's note."""
    token = await IDENTITY.agent_token(TOOL_GW, p["tenant"])
    async with http.client("tool-gateway") as c:
        r = await c.post(
            "/v1/approvals",
            json={
                "tool": "erp.post_parked_invoice",
                "args": {"invoice_document": p["invoice_document"], "amount": p["invoice"]["amount"]},
            },
            headers={"authorization": f"Bearer {token}"},
        )
    r.raise_for_status()
    note = await agent_draft(
        {**p, "kind": "approval_request", "facts": {**p["invoice"], "reason": p["reason"]}}
    )
    return {"approval_id": r.json()["approval_id"], "note": note.get("result", {}).get("text")}


async def record_outcome(p: dict) -> dict:
    entry = PROCESS_MAP.setdefault(
        p["process_id"], {"process": "vendor-invoice", "runs": [p["run_id"]], "outcome": None}
    )
    entry["outcome"] = p["outcome"]
    telemetry.record_process("vendor-invoice", p["outcome"])
    return {"recorded": True}


ACTIVITIES = {
    "register_run": register_run,
    "agent_extract": agent_extract,
    "agent_classify": agent_classify,
    "agent_draft": agent_draft,
    "sap_commit": sap_commit,
    "find_approver": find_approver,
    "request_approval": request_approval,
    "record_outcome": record_outcome,
}
