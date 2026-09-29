"""Tool registry. Developers register a tool here (contract + policy); they never embed credentials.

Each tool declares:
  side_effect   read | simulate | commit
  identity      user_required | user_or_agent | agent_only   (plus the app role an agent needs)
  schemas       JSON schema for input and output (output derived from the canonical model)
  business_key  which input field identifies the business object (for audit and spans)
  cache_ttl_s   only for safe reads; 0 disables
  timeout_s     hard budget for the vendor call
  hitl          commit needs a recorded human approval (single-use, bound to the exact arguments);
                `hitl_above` limits that to amounts over a threshold, and the system of record
                re-checks the stated amount so a caller cannot understate it to skip approval"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aiip.connectors import canonical as C
from aiip.shared.schema import check_schema


def _in(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


def _model(model: type[C.Canonical]) -> dict[str, Any]:
    return model.model_json_schema()


STR = {"type": "string", "minLength": 1, "maxLength": 200}
ACCOUNT = {"type": "string", "pattern": "^(ACC-[0-9]{4}|[A-Za-z0-9]{15,18})$"}
ORDER = {"type": "string", "pattern": "^[0-9]{7}$"}
ITEMS = {
    "type": "array",
    "minItems": 1,
    "maxItems": 20,
    "items": _in(
        {
            "material": {"type": "string", "pattern": "^MAT-[A-Z0-9-]+$"},
            "quantity": {"type": "integer", "minimum": 1, "maximum": 10000},
        },
        ["material", "quantity"],
    ),
}
MONEY = {"type": "string", "pattern": r"^[0-9]{1,9}(\.[0-9]{2})?$"}
INVOICE_DOC = {"type": "string", "pattern": "^51056[0-9]{5}$"}


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    system: str
    connector: str
    side_effect: str
    identity: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    business_key: str
    app_role: str = ""
    cache_ttl_s: float = 0
    timeout_s: float = 3.0
    hitl: bool = False
    hitl_above: float | None = None  # HITL only when args["amount"] exceeds this (None = always)
    tags: tuple[str, ...] = field(default_factory=tuple)

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "system": self.system,
            "side_effect": self.side_effect,
            "identity": self.identity,
            "app_role": self.app_role,
            "business_key": self.business_key,
            "cache_ttl_s": self.cache_ttl_s,
            "timeout_s": self.timeout_s,
            "hitl": self.hitl,
            "hitl_above": self.hitl_above,
            "idempotency_required": self.side_effect == "commit",
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        }


CASES_OUT = {
    "type": "object",
    "properties": {
        "account_id": {"type": "string"},
        "cases": {"type": "array", "items": _model(C.CaseSummary)},
    },
    "required": ["account_id", "cases"],
}
APPROVER_OUT = {
    "type": "object",
    "properties": {"approver": {"anyOf": [_model(C.Worker), {"type": "null"}]}},
    "required": ["approver"],
}
DV_INCIDENT_OUT = {
    "type": "object",
    "properties": {
        "source_system": {"const": "dataverse"},
        "ticketnumber": {"type": "string"},
        "created": {"type": "boolean"},
    },
    "required": ["ticketnumber", "created"],
}

_TOOLS = (
    ToolDef(
        "crm.get_account",
        "Customer account (canonical Customer) from the CRM.",
        "salesforce",
        "salesforce.get_account",
        "read",
        "user_or_agent",
        _in({"account_id": ACCOUNT}, ["account_id"]),
        _model(C.Customer),
        "account_id",
        "Accounts.Read.All",
        cache_ttl_s=30,
    ),
    ToolDef(
        "crm.list_cases",
        "Cases for an account.",
        "salesforce",
        "salesforce.list_cases",
        "read",
        "user_or_agent",
        _in({"account_id": ACCOUNT}, ["account_id"]),
        CASES_OUT,
        "account_id",
        "Accounts.Read.All",
        cache_ttl_s=15,
    ),
    ToolDef(
        "crm.upsert_case",
        "Create or update a case keyed by the idempotency key.",
        "salesforce",
        "salesforce.upsert_case",
        "commit",
        "user_or_agent",
        _in(
            {
                "account_id": ACCOUNT,
                "subject": STR,
                "priority": {"enum": ["Low", "Medium", "High"]},
                "description": {"type": "string", "maxLength": 2000},
            },
            ["account_id", "subject"],
        ),
        _model(C.Case),
        "account_id",
        "Cases.Write",
    ),
    ToolDef(
        "service.get_entitlement",
        "Service entitlement / SLA from Dynamics 365 (Dataverse), as the user.",
        "dataverse",
        "dataverse.get_entitlement",
        "read",
        "user_required",
        _in({"account_number": {"type": "string", "pattern": "^ACC-[0-9]{4}$"}}, ["account_number"]),
        _model(C.Entitlement),
        "account_number",
        cache_ttl_s=60,
    ),
    ToolDef(
        "service.upsert_incident",
        "Create or update a Dataverse incident by alternate key.",
        "dataverse",
        "dataverse.upsert_incident",
        "commit",
        "user_required",
        _in(
            {"account_number": {"type": "string", "pattern": "^ACC-[0-9]{4}$"}, "title": STR},
            ["account_number", "title"],
        ),
        DV_INCIDENT_OUT,
        "account_number",
    ),
    ToolDef(
        "erp.get_sales_order",
        "Sales order header in business terms.",
        "sap",
        "sap_odata.get_sales_order",
        "read",
        "user_or_agent",
        _in({"order_id": ORDER}, ["order_id"]),
        _model(C.SalesOrder),
        "order_id",
        "Orders.Read",
        cache_ttl_s=20,
    ),
    ToolDef(
        "erp.get_delivery",
        "Outbound delivery with days late.",
        "sap",
        "sap_odata.get_delivery",
        "read",
        "user_or_agent",
        _in({"delivery_id": {"type": "string", "pattern": "^8[0-9]{7}$"}}, ["delivery_id"]),
        _model(C.Delivery),
        "delivery_id",
        "Shipments.Read",
        cache_ttl_s=20,
    ),
    ToolDef(
        "erp.simulate_sales_order",
        "Price an order without persisting it.",
        "sap",
        "sap_odata.simulate_sales_order",
        "simulate",
        "user_or_agent",
        _in(
            {"customer_ref": {"type": "string", "pattern": "^ACC-[0-9]{4}$"}, "items": ITEMS},
            ["customer_ref", "items"],
        ),
        _model(C.OrderSimulation),
        "customer_ref",
        "Orders.Simulate",
    ),
    ToolDef(
        "erp.create_sales_order",
        "Create a sales order (human approval required).",
        "sap",
        "sap_odata.create_sales_order",
        "commit",
        "user_required",
        _in(
            {
                "customer_ref": {"type": "string", "pattern": "^ACC-[0-9]{4}$"},
                "items": ITEMS,
                "customer_po": {"type": "string", "maxLength": 35},
            },
            ["customer_ref", "items"],
        ),
        _model(C.SalesOrder),
        "customer_ref",
        hitl=True,
        timeout_s=5,
    ),
    ToolDef(
        "erp.get_purchase_order",
        "Purchase order with total for three-way match.",
        "sap",
        "sap_odata.get_purchase_order",
        "read",
        "user_or_agent",
        _in({"po_id": {"type": "string", "pattern": "^45000[0-9]{5}$"}}, ["po_id"]),
        _model(C.PurchaseOrder),
        "po_id",
        "PurchaseOrders.Read",
        cache_ttl_s=60,
    ),
    ToolDef(
        "erp.park_invoice",
        "Park a supplier invoice (not yet posted).",
        "sap",
        "sap_odata.park_invoice",
        "commit",
        "agent_only",
        _in(
            {
                "po_id": {"type": "string"},
                "supplier_id": STR,
                "amount": MONEY,
                "tax_code": {"type": "string", "maxLength": 4},
                "vendor_invoice_no": STR,
            },
            ["po_id", "supplier_id", "amount", "tax_code", "vendor_invoice_no"],
        ),
        _model(C.InvoiceResult),
        "vendor_invoice_no",
        "Invoices.Write",
        timeout_s=5,
    ),
    ToolDef(
        "erp.post_parked_invoice",
        "Post a parked invoice to the ledger (human approval above 10,000.00; SAP verifies the stated amount).",
        "sap",
        "sap_odata.post_parked_invoice",
        "commit",
        "agent_only",
        _in({"invoice_document": INVOICE_DOC, "amount": MONEY}, ["invoice_document", "amount"]),
        _model(C.InvoiceResult),
        "invoice_document",
        "Invoices.Write",
        hitl=True,
        hitl_above=10000.0,
        timeout_s=5,
    ),
    ToolDef(
        "erp.delete_parked_invoice",
        "Compensation: delete a parked invoice.",
        "sap",
        "sap_odata.delete_parked_invoice",
        "commit",
        "agent_only",
        _in({"invoice_document": INVOICE_DOC}, ["invoice_document"]),
        _model(C.InvoiceResult),
        "invoice_document",
        "Invoices.Write",
    ),
    ToolDef(
        "erp.reverse_invoice",
        "Compensation: reverse a posted invoice.",
        "sap",
        "sap_odata.reverse_invoice",
        "commit",
        "agent_only",
        _in({"invoice_document": INVOICE_DOC}, ["invoice_document"]),
        _model(C.InvoiceResult),
        "invoice_document",
        "Invoices.Write",
    ),
    ToolDef(
        "erp.schedule_payment",
        "Schedule payment for a posted invoice.",
        "sap",
        "sap_odata.schedule_payment",
        "commit",
        "agent_only",
        _in({"invoice_document": INVOICE_DOC}, ["invoice_document"]),
        _model(C.InvoiceResult),
        "invoice_document",
        "Invoices.Write",
    ),
    ToolDef(
        "itsm.create_incident",
        "Open an ITSM incident (idempotent on correlation id).",
        "servicenow",
        "servicenow.create_incident",
        "commit",
        "user_or_agent",
        _in(
            {
                "short_description": STR,
                "description": {"type": "string", "maxLength": 2000},
                "priority": {"type": "integer", "minimum": 1, "maximum": 5},
                "business_key": STR,
            },
            ["short_description", "business_key"],
        ),
        _model(C.Incident),
        "business_key",
        "Incidents.Write",
    ),
    ToolDef(
        "hr.get_worker",
        "Worker reference data (minimal fields).",
        "workday",
        "workday.get_worker",
        "read",
        "agent_only",
        _in({"worker_id": {"type": "string", "pattern": "^W-[0-9]{3}$"}}, ["worker_id"]),
        _model(C.Worker),
        "worker_id",
        "Workers.Read",
        cache_ttl_s=300,
    ),
    ToolDef(
        "hr.find_approver",
        "Approver for a cost center and amount.",
        "workday",
        "workday.find_approver",
        "read",
        "agent_only",
        _in(
            {
                "cost_center": {"type": "string", "pattern": "^CC-[0-9]{3}$"},
                "amount": {"type": "number", "minimum": 0},
            },
            ["cost_center", "amount"],
        ),
        APPROVER_OUT,
        "cost_center",
        "Workers.Read",
        cache_ttl_s=300,
    ),
    ToolDef(
        "eng.create_issue",
        "Open an engineering issue in Jira (idempotent label).",
        "jira",
        "jira.create_issue",
        "commit",
        "agent_only",
        _in(
            {
                "project": {"enum": ["OPS", "INT"]},
                "summary": STR,
                "description": {"type": "string", "maxLength": 2000},
                "business_key": STR,
            },
            ["project", "summary", "business_key"],
        ),
        _model(C.Issue),
        "business_key",
        "Incidents.Write",
    ),
)

TOOLS: dict[str, ToolDef] = {t.name: t for t in _TOOLS}


def validate_registry() -> list[str]:
    """Registry lint (also run in CI through tests): returns problems, empty when clean."""
    from aiip.connectors.registry import resolve

    problems = []
    for t in TOOLS.values():
        if t.side_effect not in {"read", "simulate", "commit"}:
            problems.append(f"{t.name}: bad side_effect")
        if t.identity not in {"user_required", "user_or_agent", "agent_only"}:
            problems.append(f"{t.name}: bad identity policy")
        if t.identity != "user_required" and not t.app_role:
            problems.append(f"{t.name}: agent-callable tool needs an app_role")
        if t.cache_ttl_s and t.side_effect != "read":
            problems.append(f"{t.name}: only reads may be cached")
        if t.business_key not in t.input_schema.get("properties", {}):
            problems.append(f"{t.name}: business_key not in input schema")
        try:
            check_schema(t.input_schema)
            check_schema(t.output_schema)
            resolve(t.connector)
        except Exception as exc:
            problems.append(f"{t.name}: {type(exc).__name__}")
    return problems
