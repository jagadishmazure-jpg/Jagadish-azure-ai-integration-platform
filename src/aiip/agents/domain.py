"""Domain agents. Each skill hands a MAF agent a tool plan; the tools are thin wrappers over the
gateways, bound to the principal of *this* request:

  crm-agent        user-scoped (OBO to the Tool Gateway): CRM ACLs apply to every read
  erp-agent        hybrid: order header as the user, delivery logistics via MCP as itself
  data-agent       agent-scoped: shared KPI tables through the MCP SQL server
  ap-invoice-agent agent-scoped language steps for the BPM (extract, classify, draft)"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from typing import Any

from agent_framework import tool

from aiip.a2a.server import SkillContext
from aiip.agents.gateways import call_mcp, call_tool
from aiip.agents.maf import run_agent
from aiip.identity.client import IdentityClient
from aiip.identity.registrations import MCP_GW, TOOL_GW
from aiip.shared import errors as E
from aiip.shared.tracecontext import child_traceparent

IDENTITY = {
    name: IdentityClient(name) for name in ("crm-agent", "erp-agent", "data-agent", "ap-invoice-agent")
}


class Recorder:
    """Collects typed tool results next to the MAF transcript; failures are kept as result classes."""

    def __init__(self) -> None:
        self.results: dict[str, Any] = {}
        self.failures: dict[str, str] = {}

    async def run(self, key: str, coro) -> str:
        try:
            out = await coro
            self.results[key] = out
            return json.dumps(out.get("data", out))[:2000]
        except E.GatewayError as exc:
            self.failures[key] = exc.code
            self.results[key] = {"error": exc.code, "message": exc.message}
            return json.dumps({"error": exc.code})


# ------------------------------------------------------------------ crm-agent
async def crm_handler(skill: str, args: dict[str, Any], ctx: SkillContext) -> dict[str, Any]:
    if not ctx.principal.is_user:
        raise E.GatewayError(E.AUTHZ_DENY, "crm-agent only acts on behalf of a user")
    token = await IDENTITY["crm-agent"].obo(ctx.token, TOOL_GW)
    tp = child_traceparent(ctx.traceparent)
    rec = Recorder()
    account = args.get("account_number") or args.get("customer", {}).get("account_number")

    @tool(name="get_account", description="CRM account by account number")
    async def get_account(account_id: str) -> str:
        return await rec.run(
            "account", call_tool("crm.get_account", {"account_id": account_id}, token=token, traceparent=tp)
        )

    @tool(name="list_cases", description="Cases for an account")
    async def list_cases(account_id: str) -> str:
        return await rec.run(
            "cases", call_tool("crm.list_cases", {"account_id": account_id}, token=token, traceparent=tp)
        )

    @tool(name="get_entitlement", description="Service entitlement (SLA) in Dynamics 365")
    async def get_entitlement(account_number: str) -> str:
        return await rec.run(
            "entitlement",
            call_tool(
                "service.get_entitlement", {"account_number": account_number}, token=token, traceparent=tp
            ),
        )

    @tool(name="upsert_case", description="Create or update a CRM case (idempotent)")
    async def upsert_case(account_id: str, subject: str, priority: str, idempotency_key: str) -> str:
        return await rec.run(
            "case",
            call_tool(
                "crm.upsert_case",
                {"account_id": account_id, "subject": subject, "priority": priority},
                token=token,
                traceparent=tp,
                idempotency_key=idempotency_key,
            ),
        )

    instructions = (
        "You are the CRM agent. Use only the tools; never guess account data. The user's sharing rules apply."
    )
    if skill == "account_summary":
        await run_agent(
            "crm-agent",
            instructions,
            [get_account, list_cases, get_entitlement],
            [
                {"tool": "get_account", "args": {"account_id": account}},
                {"tool": "list_cases", "args": {"account_id": account}},
                {"tool": "get_entitlement", "args": {"account_number": account}},
            ],
        )
        if "account" in rec.failures:
            raise E.GatewayError(rec.failures["account"], "account not available to this user")
        return {
            "account": rec.results["account"]["data"],
            "open_cases": [
                c
                for c in rec.results.get("cases", {}).get("data", {}).get("cases", [])
                if c["status"] != "Closed"
            ],
            "entitlement": rec.results.get("entitlement", {}).get("data"),
            "degraded": rec.failures,
            "acting_as": ctx.principal.subject,
        }
    if skill == "open_case":
        key = (
            args.get("idempotency_key")
            or "case-" + hashlib.sha256(f"{account}|{args['subject']}".encode()).hexdigest()[:16]
        )
        await run_agent(
            "crm-agent",
            instructions,
            [upsert_case],
            [
                {
                    "tool": "upsert_case",
                    "args": {
                        "account_id": account,
                        "subject": args["subject"],
                        "priority": args.get("priority", "Medium"),
                        "idempotency_key": key,
                    },
                }
            ],
        )
        if "case" in rec.failures:
            raise E.GatewayError(rec.failures["case"], rec.results["case"]["message"])
        return {
            "case": rec.results["case"]["data"],
            "replayed": rec.results["case"].get("replayed", False),
            "acting_as": ctx.principal.subject,
        }
    raise E.GatewayError(E.NOT_FOUND, skill)


# ------------------------------------------------------------------ erp-agent (hybrid)
async def erp_handler(skill: str, args: dict[str, Any], ctx: SkillContext) -> dict[str, Any]:
    tp = child_traceparent(ctx.traceparent)
    user_token = await IDENTITY["erp-agent"].obo(ctx.token, TOOL_GW) if ctx.principal.is_user else None
    agent_token = await IDENTITY["erp-agent"].agent_token(
        MCP_GW, "contoso" if ctx.principal.tenant == "contoso" else ctx.principal.tenant
    )
    rec = Recorder()

    @tool(name="get_sales_order", description="Sales order header (as the user)")
    async def get_sales_order(order_id: str) -> str:
        return await rec.run(
            "order",
            call_tool(
                "erp.get_sales_order", {"order_id": order_id}, token=user_token or agent_token, traceparent=tp
            ),
        )

    @tool(name="get_delivery", description="Delivery logistics via the SAP MCP server (agent identity)")
    async def get_delivery(delivery_id: str) -> str:
        return await rec.run(
            "delivery",
            call_mcp(
                "sap-orders", "get_delivery", {"delivery_id": delivery_id}, token=agent_token, traceparent=tp
            ),
        )

    @tool(name="simulate_order", description="Price an order without saving it")
    async def simulate_order(customer_ref: str, items: list[dict]) -> str:
        return await rec.run(
            "simulation",
            call_tool(
                "erp.simulate_sales_order",
                {"customer_ref": customer_ref, "items": items},
                token=user_token or agent_token,
                traceparent=tp,
            ),
        )

    instructions = "You are the SAP order agent. Translate SAP status codes into plain business terms."
    if skill == "order_status":
        calls = [{"tool": "get_sales_order", "args": {"order_id": args["order_id"]}}]
        delivery_id = args.get("delivery_id") or {"4500001": "80000001", "4500002": "80000002"}.get(
            args["order_id"]
        )
        if delivery_id:
            calls.append({"tool": "get_delivery", "args": {"delivery_id": delivery_id}})
        await run_agent("erp-agent", instructions, [get_sales_order, get_delivery], calls)
        if "order" in rec.failures:
            raise E.GatewayError(rec.failures["order"], "order not available")
        delivery = rec.results.get("delivery", {}).get("data")
        return {
            "order": rec.results["order"]["data"],
            "delivery": delivery,
            "identity": {"order": "obo" if user_token else "agent", "delivery": "agent"},
            "degraded": rec.failures,
        }
    if skill == "simulate_order":
        await run_agent(
            "erp-agent",
            instructions,
            [simulate_order],
            [
                {
                    "tool": "simulate_order",
                    "args": {"customer_ref": args["customer_ref"], "items": args["items"]},
                }
            ],
        )
        if "simulation" in rec.failures:
            raise E.GatewayError(rec.failures["simulation"], "simulation failed")
        return {"simulation": rec.results["simulation"]["data"]}
    raise E.GatewayError(E.NOT_FOUND, skill)


# ------------------------------------------------------------------ data-agent
async def data_handler(skill: str, args: dict[str, Any], ctx: SkillContext) -> dict[str, Any]:
    token = await IDENTITY["data-agent"].agent_token(
        MCP_GW, ctx.principal.tenant if ctx.principal.tenant in {"contoso", "fabrikam"} else "contoso"
    )
    rec = Recorder()
    region = args["region"]

    @tool(name="run_query", description="Read-only SQL over allow-listed KPI tables")
    async def run_query(sql: str) -> str:
        return await rec.run(
            "kpis",
            call_mcp(
                "sql-warehouse",
                "run_query",
                {"sql": sql},
                token=token,
                traceparent=child_traceparent(ctx.traceparent),
            ),
        )

    sql = (
        f"SELECT week, on_time_pct, late_shipments FROM delivery_kpis WHERE region = '{region}' ORDER BY week"
    )
    await run_agent(
        "data-agent",
        "You own the warehouse dialect. Only SELECT from exposed tables.",
        [run_query],
        [{"tool": "run_query", "args": {"sql": sql}}],
    )
    if "kpis" in rec.failures:
        raise E.GatewayError(rec.failures["kpis"], "kpis unavailable")
    d = rec.results["kpis"]["data"]
    rows = [dict(zip(d["columns"], r, strict=False)) for r in d["rows"]]
    return {"region": region, "weeks": rows, "identity": "agent", "source": "sql-warehouse (MCP)"}


# ------------------------------------------------------------------ ap-invoice-agent
_INV = {
    "vendor_invoice_no": re.compile(r"invoice\s*(?:no\.?|number|#)\s*[:#]?\s*([A-Z]{2,4}-\d{3,6})", re.I),
    "po_id": re.compile(r"\bPO\s*[:#]?\s*(45000\d{5})", re.I),
    "supplier_id": re.compile(r"\bvendor\s*(?:id)?\s*[:#]?\s*(V-\d{2,4})", re.I),
    "amount": re.compile(r"\btotal\s*(?:due)?\s*[:#]?\s*(?:USD|\$)?\s*([\d,]+\.\d{2})", re.I),
    "tax_code": re.compile(r"\btax\s*code\s*[:#]?\s*([A-Z]\d)", re.I),
}


async def ap_handler(skill: str, args: dict[str, Any], ctx: SkillContext) -> dict[str, Any]:
    rec = Recorder()
    if skill == "extract":
        found: dict[str, str] = {}

        @tool(name="parse_invoice", description="Deterministic field parser for invoice text")
        async def parse_invoice(text: str) -> str:
            for k, rx in _INV.items():
                m = rx.search(text)
                if m:
                    found[k] = m.group(1).replace(",", "")
            return json.dumps(found)

        await run_agent(
            "ap-invoice-agent",
            "Extract invoice fields. Never invent a value.",
            [parse_invoice],
            [{"tool": "parse_invoice", "args": {"text": args["text"]}}],
        )
        missing = sorted(set(_INV) - set(found))
        return {"invoice": found, "missing": missing, "confidence": round(1 - len(missing) / len(_INV), 2)}
    if skill == "classify":
        inv = args["invoice"]
        token = await IDENTITY["ap-invoice-agent"].agent_token(TOOL_GW, ctx.principal.tenant)

        @tool(name="get_po", description="Purchase order total for three-way match")
        async def get_po(po_id: str) -> str:
            return await rec.run(
                "po",
                call_tool(
                    "erp.get_purchase_order",
                    {"po_id": po_id},
                    token=token,
                    traceparent=child_traceparent(ctx.traceparent),
                ),
            )

        await run_agent(
            "ap-invoice-agent",
            "Classify the invoice against its PO.",
            [get_po],
            [{"tool": "get_po", "args": {"po_id": inv.get("po_id", "")}}],
        )
        if "po" in rec.failures:
            return {"route": "reject", "reason": f"purchase order check failed ({rec.failures['po']})"}
        po = rec.results["po"]["data"]
        amount, total = Decimal(inv["amount"]), Decimal(po["total"])
        variance = abs(amount - total) / total if total else Decimal(1)
        if variance > Decimal("0.02"):
            route, reason = "review", f"amount differs from PO by {variance:.1%}"
        elif amount > Decimal("10000"):
            route, reason = "review", "above auto-approval threshold (10,000.00)"
        else:
            route, reason = "auto", "matches PO within 2%"
        return {
            "route": route,
            "reason": reason,
            "po_total": po["total"],
            "cost_center": po["cost_center"],
            "supplier_matches": po["supplier_id"] == inv.get("supplier_id"),
        }
    if skill == "draft":
        facts = args["facts"]
        templates = {
            "approval_request": "Invoice {vendor_invoice_no} from {supplier_id} for {amount} needs your approval: {reason}.",
            "rejection": "Invoice {vendor_invoice_no} was not approved ({reason}). Please contact accounts payable.",
            "remittance": "Invoice {vendor_invoice_no} was posted as {invoice_document}; payment is scheduled.",
            "delay_notice": "Order {order_id} is running {days_late} day(s) late; revised delivery {revised_date}.",
        }
        text = templates[args["kind"]].format_map(
            {k: facts.get(k, "n/a") for k in re.findall(r"{(\w+)}", templates[args["kind"]])}
        )
        await run_agent("ap-invoice-agent", "Draft a short, factual note.", [], [], compose=text)
        return {"kind": args["kind"], "text": text}
    raise E.GatewayError(E.NOT_FOUND, skill)
