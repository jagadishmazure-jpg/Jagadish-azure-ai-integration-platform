"""ASGI apps for each A2A agent (one process per agent in the topology / Container Apps)."""

from __future__ import annotations

from aiip.a2a.server import create_agent_app
from aiip.a2a.specs import versions
from aiip.agents.domain import ap_handler, crm_handler, data_handler, erp_handler
from aiip.agents.planner import planner_handler

HANDLERS = {
    "care-planner": planner_handler,
    "crm-agent": crm_handler,
    "erp-agent": erp_handler,
    "data-agent": data_handler,
    "ap-invoice-agent": ap_handler,
}


def build(agent_id: str):
    return create_agent_app(
        sorted(versions(agent_id), key=lambda s: tuple(int(x) for x in s.version.split("."))),
        HANDLERS[agent_id],
    )


care_planner_app = build("care-planner")
crm_app = build("crm-agent")
erp_app = build("erp-agent")
data_app = build("data-agent")
ap_invoice_app = build("ap-invoice-agent")
