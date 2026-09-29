"""Graphs started by events. Each run is agent-scoped (the worker's own identity), bounded by a
step budget, and returns an outcome plus the completion event to emit."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from aiip.a2a import client as a2a
from aiip.agents.gateways import call_mcp, call_tool
from aiip.identity.client import IdentityClient
from aiip.identity.registrations import MCP_GW, TOOL_GW, agent_uri
from aiip.shared import errors as E
from aiip.shared.tracecontext import child_traceparent


@dataclass
class RunContext:
    event: dict[str, Any]
    identity: IdentityClient
    tenant: str
    traceparent: str
    run_id: str = field(default_factory=lambda: "run-" + uuid.uuid4().hex[:12])
    max_steps: int = 8
    steps: int = 0
    trail: list[str] = field(default_factory=list)

    def step(self, name: str) -> str:
        self.steps += 1
        if self.steps > self.max_steps:
            raise E.GatewayError(E.BUDGET, f"graph step budget ({self.max_steps}) exhausted")
        self.trail.append(name)
        return child_traceparent(self.traceparent)


async def order_created(ctx: RunContext) -> dict[str, Any]:
    """Credit-hold triage for a new SAP order. Blocked orders get one ITSM incident (idempotent)."""
    d = ctx.event["data"]
    token = await ctx.identity.agent_token(TOOL_GW, ctx.tenant)
    order = (
        await call_tool(
            "erp.get_sales_order",
            {"order_id": d["order_id"]},
            token=token,
            traceparent=ctx.step("get_sales_order"),
        )
    )["data"]
    acct = (
        await call_tool(
            "crm.get_account",
            {"account_id": order["customer_ref"]},
            token=token,
            traceparent=ctx.step("get_account"),
        )
    )["data"]
    incident = None
    if acct["credit_hold"]:
        mcp_token = await ctx.identity.agent_token(MCP_GW, ctx.tenant)
        open_items = (
            await call_mcp(
                "servicenow-incidents",
                "list_incidents",
                {"category": "credit"},
                token=mcp_token,
                traceparent=ctx.step("check_open_incidents"),
            )
        )["data"]
        res = await call_tool(
            "itsm.create_incident",
            {
                "short_description": f"Order {order['order_id']} blocked: customer {acct['account_number']} on credit hold",
                "priority": 3,
                "business_key": f"SO-{order['order_id']}",
            },
            token=token,
            traceparent=ctx.step("create_incident"),
            idempotency_key=f"order-created-{order['order_id']}",
        )
        incident = res["data"]["number"]
        outcome = "blocked_credit_hold"
        ctx.trail.append(f"open_credit_incidents={len(open_items.get('incidents', []))}")
    else:
        outcome = "cleared"
    return {
        "outcome": outcome,
        "completion": (
            "OrderTriaged",
            {"order_id": order["order_id"], "outcome": outcome, "run_id": ctx.run_id, "incident": incident},
        ),
    }


async def shipment_late(ctx: RunContext) -> dict[str, Any]:
    """Proactive late-delivery handling: facts from SAP, a re-ship quote (simulate only), a drafted
    notice from the AP/notes agent over A2A, and one CRM case keyed on the delivery."""
    d = ctx.event["data"]
    token = await ctx.identity.agent_token(TOOL_GW, ctx.tenant)
    delivery = (
        await call_tool(
            "erp.get_delivery",
            {"delivery_id": d["delivery_id"]},
            token=token,
            traceparent=ctx.step("get_delivery"),
        )
    )["data"]
    order = (
        await call_tool(
            "erp.get_sales_order",
            {"order_id": delivery["order_id"]},
            token=token,
            traceparent=ctx.step("get_sales_order"),
        )
    )["data"]
    quote = (
        await call_tool(
            "erp.simulate_sales_order",
            {"customer_ref": order["customer_ref"], "items": [{"material": "MAT-PALLET-02", "quantity": 1}]},
            token=token,
            traceparent=ctx.step("simulate_reship"),
        )
    )["data"]
    a2a_token = await ctx.identity.agent_token(agent_uri("ap-invoice-agent"), ctx.tenant)
    note = await a2a.send(
        "ap-invoice-agent",
        "draft",
        {
            "kind": "delay_notice",
            "facts": {
                "order_id": order["order_id"],
                "days_late": delivery["days_late"],
                "revised_date": delivery["revised_date"],
            },
        },
        token=a2a_token,
        tenant_id=ctx.event["tenantid"],
        traceparent=ctx.step("draft_notice"),
    )
    case = await call_tool(
        "crm.upsert_case",
        {
            "account_id": order["customer_ref"],
            "subject": f"Proactive: delivery {delivery['delivery_id']} {delivery['days_late']} day(s) late",
            "priority": "High",
            "description": note["output"]["text"],
        },
        token=token,
        traceparent=ctx.step("upsert_case"),
        idempotency_key=f"shipment-late-{delivery['delivery_id']}",
    )
    return {
        "outcome": "customer_notified",
        "reship_quote": quote["net_amount"],
        "completion": (
            "ShipmentLateHandled",
            {
                "delivery_id": delivery["delivery_id"],
                "outcome": "customer_notified",
                "run_id": ctx.run_id,
                "case_id": case["data"]["case_id"],
            },
        ),
    }


GRAPHS = {
    "com.contoso.sap.salesorder.created.v1": ("worker-order-events", order_created),
    "com.contoso.sap.delivery.late.v1": ("worker-shipment-events", shipment_late),
}
