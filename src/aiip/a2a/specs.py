"""Agent contracts: the source of truth for agent cards, tool allow-lists and caller allow-lists.

Every principal that can call the integration plane has an entry here, including event workers
and the BPM orchestrator (they have cards but no A2A endpoint). Eval scores are *not* typed in
by hand: they are read from `evals/scores.json`, which the eval gate writes after a real run."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SCORES_FILE = Path(__file__).resolve().parents[3] / "evals" / "scores.json"


@dataclass(frozen=True)
class SkillSpec:
    id: str
    name: str
    description: str
    side_effect: str  # read | simulate | commit
    input_schema: dict[str, Any]
    hitl: bool = False


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    description: str
    owner: str
    version: str
    kind: str  # a2a-agent | worker | orchestrator
    identity_mode: str  # obo | agent | hybrid
    skills: tuple[SkillSpec, ...] = ()
    allowed_callers: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    sla_p95_ms: int = 2000
    sla_availability: str = "99.5%"
    system_of_record: str = ""
    service: str = ""  # logical service name (aiip.shared.http) for A2A agents
    rpc_path: str = "/a2a"
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def side_effect_class(self) -> str:
        """Highest side effect across the skills *and* the tools the card allows (a worker with no
        skills but a commit tool is a committing agent)."""
        from aiip.tools.registry import TOOLS

        order = ["read", "simulate", "commit"]
        effects = [s.side_effect for s in self.skills] + [
            TOOLS[t].side_effect for t in self.tools if t in TOOLS
        ]
        return max(effects, key=order.index, default="read")

    @property
    def key(self) -> str:
        return f"{self.id}@{self.version}"

    def skill(self, skill_id: str) -> SkillSpec | None:
        return next((s for s in self.skills if s.id == skill_id), None)

    @property
    def eval_score(self) -> float | None:
        return eval_scores().get(self.id)


def eval_scores() -> dict[str, float]:
    path = Path(os.environ.get("AIIP_EVAL_SCORES", SCORES_FILE))
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    return {k: v["score"] for k, v in data.get("agents", {}).items()}


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


ACCOUNT = {"type": "string", "pattern": "^ACC-[0-9]{4}$"}
ORDER = {"type": "string", "pattern": "^[0-9]{7}$"}
OWNER = "Jagadish Meduri (integration engineering)"

AGENTS: tuple[AgentSpec, ...] = (
    AgentSpec(
        id="care-planner",
        name="Customer-care planner",
        description="Interactive planner for care reps. Fans out to CRM, ERP and data agents over A2A and composes one answer.",
        owner=OWNER,
        version="1.0.0",
        kind="a2a-agent",
        identity_mode="obo",
        skills=(
            SkillSpec(
                "resolve_customer_request",
                "Resolve customer request",
                "Account, order and delivery picture for one customer question; may open a case.",
                "commit",
                _obj(
                    {
                        "account_number": ACCOUNT,
                        "order_id": ORDER,
                        "question": {"type": "string", "maxLength": 500},
                        "open_case": {"type": "boolean"},
                    },
                    ["account_number", "question"],
                ),
            ),
        ),
        allowed_callers=("experience-bff",),
        sla_p95_ms=4000,
        service="agent-care-planner",
    ),
    AgentSpec(
        id="crm-agent",
        name="CRM agent",
        description="Owns the customer object model in the CRM (Salesforce stand-in). Always acts as the user.",
        owner=OWNER,
        version="1.3.0",
        kind="a2a-agent",
        identity_mode="obo",
        skills=(
            SkillSpec(
                "account_summary",
                "Account summary",
                "Account plus open cases, trimmed by the user's sharing rules.",
                "read",
                _obj({"account_number": ACCOUNT}, ["account_number"]),
            ),
            SkillSpec(
                "open_case",
                "Open case",
                "Create or update a CRM case (idempotent on the caller's key).",
                "commit",
                _obj(
                    {
                        "account_number": ACCOUNT,
                        "subject": {"type": "string", "maxLength": 200},
                        "priority": {"enum": ["Low", "Medium", "High"]},
                        "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 80},
                    },
                    ["account_number", "subject"],
                ),
            ),
        ),
        allowed_callers=("care-planner",),
        tools=("crm.get_account", "crm.list_cases", "crm.upsert_case", "service.get_entitlement"),
        sla_p95_ms=1500,
        system_of_record="salesforce, dataverse",
        service="agent-crm",
    ),
    AgentSpec(
        id="crm-agent",
        name="CRM agent",
        description="v2 contract: `customer` object replaces `account_number`. Published alongside v1 during migration.",
        owner=OWNER,
        version="2.0.0",
        kind="a2a-agent",
        identity_mode="obo",
        skills=(
            SkillSpec(
                "account_summary",
                "Account summary",
                "Account plus open cases (v2 input shape).",
                "read",
                _obj({"customer": _obj({"account_number": ACCOUNT}, ["account_number"])}, ["customer"]),
            ),
        ),
        allowed_callers=("care-planner",),
        tools=("crm.get_account", "crm.list_cases", "service.get_entitlement"),
        sla_p95_ms=1500,
        system_of_record="salesforce, dataverse",
        service="agent-crm",
        rpc_path="/v2/a2a",
    ),
    AgentSpec(
        id="erp-agent",
        name="ERP / SAP agent",
        description="Owns SAP sales-order and delivery semantics (status codes, simulation). Hybrid identity.",
        owner=OWNER,
        version="1.1.0",
        kind="a2a-agent",
        identity_mode="hybrid",
        skills=(
            SkillSpec(
                "order_status",
                "Order status",
                "Sales order plus delivery status in business terms.",
                "read",
                _obj({"order_id": ORDER, "delivery_id": {"type": "string"}}, ["order_id"]),
            ),
            SkillSpec(
                "simulate_order",
                "Simulate order",
                "Price a proposed order without persisting it.",
                "simulate",
                _obj(
                    {"customer_ref": ACCOUNT, "items": {"type": "array", "minItems": 1}},
                    ["customer_ref", "items"],
                ),
            ),
        ),
        allowed_callers=("care-planner",),
        tools=("erp.get_sales_order", "erp.get_delivery", "erp.simulate_sales_order"),
        mcp_servers=("sap-orders",),
        sla_p95_ms=2500,
        system_of_record="sap",
        service="agent-erp",
    ),
    AgentSpec(
        id="data-agent",
        name="Data / analytics agent",
        description="Owns the SQL dialect and table permissions of the analytics warehouse; reached through the MCP gateway.",
        owner=OWNER,
        version="1.0.0",
        kind="a2a-agent",
        identity_mode="agent",
        skills=(
            SkillSpec(
                "delivery_kpis",
                "Delivery KPIs",
                "On-time and late-shipment KPIs by region.",
                "read",
                _obj({"region": {"enum": ["east", "west"]}}, ["region"]),
            ),
        ),
        allowed_callers=("care-planner",),
        mcp_servers=("sql-warehouse",),
        sla_p95_ms=3000,
        system_of_record="databricks-style warehouse",
        service="agent-data",
    ),
    AgentSpec(
        id="ap-invoice-agent",
        name="AP invoice agent",
        description="Language steps for accounts payable: extract fields, classify, draft notes. No commits.",
        owner=OWNER,
        version="1.0.0",
        kind="a2a-agent",
        identity_mode="agent",
        skills=(
            SkillSpec(
                "extract",
                "Extract",
                "Pull invoice fields from vendor text.",
                "read",
                _obj({"text": {"type": "string", "maxLength": 4000}}, ["text"]),
            ),
            SkillSpec(
                "classify",
                "Classify",
                "Route an invoice: auto, review or reject.",
                "read",
                _obj({"invoice": {"type": "object"}, "po_total": {"type": "string"}}, ["invoice"]),
            ),
            SkillSpec(
                "draft",
                "Draft note",
                "Draft a vendor or approver note from structured facts.",
                "read",
                _obj(
                    {
                        "kind": {"enum": ["approval_request", "rejection", "remittance", "delay_notice"]},
                        "facts": {"type": "object"},
                    },
                    ["kind", "facts"],
                ),
            ),
        ),
        allowed_callers=("bpm-invoice-orchestrator", "worker-shipment-events"),
        tools=("erp.get_purchase_order",),
        sla_p95_ms=3000,
        service="agent-ap-invoice",
    ),
    AgentSpec(
        id="worker-order-events",
        name="OrderCreated worker",
        description="Event-driven graph for SAP OrderCreated: credit-hold check, ITSM incident when blocked.",
        owner=OWNER,
        version="1.0.0",
        kind="worker",
        identity_mode="agent",
        tools=("erp.get_sales_order", "crm.get_account", "itsm.create_incident"),
        mcp_servers=("servicenow-incidents",),
    ),
    AgentSpec(
        id="worker-shipment-events",
        name="ShipmentLate worker",
        description="Event-driven graph for ShipmentLate: delivery facts, re-plan simulation, proactive CRM case.",
        owner=OWNER,
        version="1.0.0",
        kind="worker",
        identity_mode="agent",
        tools=("erp.get_delivery", "erp.get_sales_order", "erp.simulate_sales_order", "crm.upsert_case"),
    ),
    AgentSpec(
        id="bpm-invoice-orchestrator",
        name="Vendor invoice process (Durable Functions)",
        description="BPM parent for vendor invoices: park, approve (HITL), post, schedule payment, compensate.",
        owner=OWNER,
        version="1.0.0",
        kind="orchestrator",
        identity_mode="agent",
        tools=(
            "erp.get_purchase_order",
            "erp.park_invoice",
            "erp.post_parked_invoice",
            "erp.delete_parked_invoice",
            "erp.reverse_invoice",
            "erp.schedule_payment",
            "hr.find_approver",
            "itsm.create_incident",
            "eng.create_issue",
        ),
    ),
)


def latest(agent_id: str) -> AgentSpec | None:
    versions = [a for a in AGENTS if a.id == agent_id]
    return max(versions, key=lambda a: tuple(int(x) for x in a.version.split("."))) if versions else None


def versions(agent_id: str) -> list[AgentSpec]:
    return [a for a in AGENTS if a.id == agent_id]


def principal_spec(actor: str) -> AgentSpec | None:
    """Allow-list lookup for a calling principal: union of tools across its published versions."""
    return latest(actor)


def allowed_tools(actor: str) -> set[str]:
    return {t for a in AGENTS if a.id == actor for t in a.tools}


def allowed_mcp_servers(actor: str) -> set[str]:
    return {s for a in AGENTS if a.id == actor for s in a.mcp_servers}
