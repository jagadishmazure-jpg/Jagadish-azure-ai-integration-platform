"""Customer-care planner. Interactive, user-scoped: every downstream hop carries the user.

Phase 1 fans out in parallel to the CRM agent (account, cases, SLA) and the ERP agent (order and
delivery). Phase 2 asks the data agent for regional delivery KPIs (shared reference data, read by
that agent with its own identity: the hybrid pattern). If the order is late and the rep asked for
it, the planner opens a CRM case with a deterministic idempotency key, so a retry never duplicates."""

from __future__ import annotations

import hashlib
from typing import Any

from agent_framework import tool

from aiip.a2a import client as a2a
from aiip.a2a.server import SkillContext
from aiip.agents.domain import Recorder
from aiip.agents.maf import run_agent
from aiip.identity.client import IdentityClient
from aiip.identity.registrations import agent_uri
from aiip.shared import errors as E

IDENTITY = IdentityClient("care-planner")


async def _ask(
    rec: Recorder,
    key: str,
    callee: str,
    skill: str,
    payload: dict,
    ctx: SkillContext,
    version: str | None = None,
) -> str:
    async def go():
        token = await IDENTITY.obo(ctx.token, agent_uri(callee))
        out = await a2a.send(
            callee,
            skill,
            payload,
            token=token,
            tenant_id=ctx.tenant_id,
            traceparent=ctx.traceparent,
            hops=ctx.hops,
            version=version,
        )
        return out

    return await rec.run(key, go())


async def planner_handler(skill: str, args: dict[str, Any], ctx: SkillContext) -> dict[str, Any]:
    if skill != "resolve_customer_request":
        raise E.GatewayError(E.NOT_FOUND, skill)
    if not ctx.principal.is_user:
        raise E.GatewayError(E.AUTHZ_DENY, "the care planner serves signed-in users only")
    rec = Recorder()
    account, order = args["account_number"], args.get("order_id")

    @tool(name="ask_crm", description="Ask the CRM agent for an account summary")
    async def ask_crm(account_number: str) -> str:
        return await _ask(
            rec, "crm", "crm-agent", "account_summary", {"account_number": account_number}, ctx, version="1"
        )

    @tool(name="ask_erp", description="Ask the ERP agent for order and delivery status")
    async def ask_erp(order_id: str) -> str:
        return await _ask(rec, "erp", "erp-agent", "order_status", {"order_id": order_id}, ctx)

    @tool(name="ask_data", description="Ask the data agent for delivery KPIs of a region")
    async def ask_data(region: str) -> str:
        return await _ask(rec, "data", "data-agent", "delivery_kpis", {"region": region}, ctx)

    instructions = "You are a customer-care planner. Delegate to domain agents; never call systems directly."
    phase1 = [{"tool": "ask_crm", "args": {"account_number": account}}]
    if order:
        phase1.append({"tool": "ask_erp", "args": {"order_id": order}})
    await run_agent("care-planner", instructions, [ask_crm, ask_erp], phase1)
    if "crm" in rec.failures:
        code = rec.failures["crm"]
        return {
            "answer": f"I can't show account {account} to you ({code}).",
            "result_class": code,
            "degraded": rec.failures,
        }
    crm = rec.results["crm"]["output"]
    region = crm["account"]["region"]
    await run_agent(
        "care-planner", instructions, [ask_data], [{"tool": "ask_data", "args": {"region": region}}]
    )

    erp = rec.results.get("erp", {}).get("output") if "erp" not in rec.failures else None
    kpis = rec.results.get("data", {}).get("output") if "data" not in rec.failures else None
    late = bool(erp and erp.get("delivery") and erp["delivery"].get("days_late", 0) > 0)
    lines = [f"{crm['account']['name']} ({crm['account']['tier']}, {region})."]
    if crm.get("entitlement"):
        lines.append(f"SLA: {crm['entitlement']['name']}.")
    if erp:
        o = erp["order"]
        lines.append(f"Order {o['order_id']} is {o['status']}, requested for {o['requested_delivery_date']}.")
        if late:
            d = erp["delivery"]
            lines.append(
                f"Delivery {d['delivery_id']} is {int(d['days_late'])} day(s) late (revised {d['revised_date']}, carrier {d['carrier']})."
            )
    elif order:
        lines.append(f"Order status is unavailable right now ({rec.failures.get('erp')}).")
    if kpis and kpis.get("weeks"):
        w = kpis["weeks"][-1]
        lines.append(f"Region on-time rate last week: {float(w['on_time_pct']):.0%}.")

    case = None
    if args.get("open_case") and late:
        key = "care-" + hashlib.sha256(f"{ctx.tenant_id}|{account}|{order}|late".encode()).hexdigest()[:20]
        await run_agent("care-planner", instructions, [], [])
        await rec.run("case", _open_case(account, order, key, ctx))
        if "case" in rec.failures:
            lines.append(f"Could not open a case ({rec.failures['case']}).")
        else:
            case = rec.results["case"]["output"]
            lines.append(
                f"Case {case['case']['case_id']} {'already existed' if case['replayed'] else 'opened'} for the late delivery."
            )
    return {
        "answer": " ".join(lines),
        "result_class": "ok" if not rec.failures else "partial",
        "account": crm["account"],
        "order": erp,
        "kpis": kpis,
        "case": case,
        "degraded": rec.failures,
        "acting_as": ctx.principal.subject,
        "fan_out": sorted(k for k in rec.results if k != "case"),
    }


async def _open_case(account: str, order: str | None, key: str, ctx: SkillContext) -> dict:
    token = await IDENTITY.obo(ctx.token, agent_uri("crm-agent"))
    return await a2a.send(
        "crm-agent",
        "open_case",
        {
            "account_number": account,
            "subject": f"Late delivery on order {order}",
            "priority": "High",
            "idempotency_key": key,
        },
        token=token,
        tenant_id=ctx.tenant_id,
        traceparent=ctx.traceparent,
        hops=ctx.hops,
        version="1",
    )
