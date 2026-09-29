"""App registration catalog: one Entra app registration per agent, worker and gateway.

Nothing here is secret. `secret_ref` is only used in local mode (stand-in for the workload's
managed identity); in Azure each registration trusts its workload's user-assigned managed identity
through a federated identity credential, so no client secret exists at all."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from aiip.config import stable_id

TOOL_GW = "api://aiip-tool-gateway"
MCP_GW = "api://aiip-mcp-gateway"
MCP_SERVERS = "api://aiip-mcp-servers"
EVENT_GW = "api://aiip-event-gateway"
DATAVERSE = "https://contoso.crm.dynamics.com"


def agent_uri(agent_id: str) -> str:
    return f"api://aiip-{agent_id}"


@dataclass(frozen=True)
class AppRegistration:
    name: str
    kind: str  # agent | worker | gateway | orchestrator
    description: str
    obo_targets: tuple[str, ...] = ()  # downstream APIs this app may call *as the user*
    app_roles: dict[str, tuple[str, ...]] = field(default_factory=dict)  # target -> roles granted to the app
    exposes_scopes: tuple[str, ...] = ("user_impersonation",)

    @property
    def client_id(self) -> str:
        """Azure: the real application (client) id from AIIP_APPID_<NAME>. Local: a stable fake GUID."""
        env = os.environ.get("AIIP_APPID_" + self.name.upper().replace("-", "_"))
        return env or stable_id(f"app:{self.name}")

    @property
    def service_principal_oid(self) -> str:
        return stable_id(f"sp:{self.name}")

    @property
    def app_id_uri(self) -> str:
        return f"api://aiip-{self.name}"

    @property
    def secret_ref(self) -> str:
        return f"kv://aiip-local-kv/{self.name}-client-auth"

    @property
    def managed_identity(self) -> str:
        return f"id-{self.name}"


_AGENT_INVOKE = ("Agent.Invoke",)

REGISTRATIONS: dict[str, AppRegistration] = {
    r.name: r
    for r in (
        AppRegistration(
            "experience-bff",
            "client",
            "Care-rep web app (public client, auth code + PKCE). Signs the user in; holds no downstream rights.",
            exposes_scopes=(),
        ),
        AppRegistration(
            "care-planner",
            "agent",
            "Customer-care planner. Interactive; acts as the signed-in user.",
            obo_targets=(agent_uri("crm-agent"), agent_uri("erp-agent"), agent_uri("data-agent")),
        ),
        AppRegistration(
            "crm-agent",
            "agent",
            "CRM domain agent (Salesforce stand-in). User-scoped only: no app roles on CRM tools.",
            obo_targets=(TOOL_GW,),
        ),
        AppRegistration(
            "erp-agent",
            "agent",
            "ERP/SAP domain agent. Hybrid: user token for customer data, own identity for order reference data.",
            obo_targets=(TOOL_GW,),
            app_roles={TOOL_GW: ("Orders.Read",), MCP_GW: ("Orders.Read",)},
        ),
        AppRegistration(
            "data-agent",
            "agent",
            "Analytics agent. Reads shared KPI tables through the MCP gateway with its own identity.",
            obo_targets=(MCP_GW,),
            app_roles={MCP_GW: ("Analytics.Read",)},
        ),
        AppRegistration(
            "ap-invoice-agent",
            "agent",
            "Accounts-payable language agent (extract, classify, draft). Called by the BPM as an activity.",
            app_roles={TOOL_GW: ("PurchaseOrders.Read",)},
        ),
        AppRegistration(
            "worker-order-events",
            "worker",
            "Event worker for OrderCreated. Agent-scoped, no user.",
            app_roles={
                TOOL_GW: ("Orders.Read", "Accounts.Read.All", "Incidents.Write"),
                MCP_GW: ("Incidents.Read", "Incidents.Write"),
                EVENT_GW: ("Events.Publish",),
            },
        ),
        AppRegistration(
            "worker-shipment-events",
            "worker",
            "Event worker for ShipmentLate. Agent-scoped, no user.",
            app_roles={
                TOOL_GW: ("Orders.Read", "Shipments.Read", "Orders.Simulate", "Cases.Write"),
                agent_uri("ap-invoice-agent"): _AGENT_INVOKE,
                EVENT_GW: ("Events.Publish",),
            },
        ),
        AppRegistration(
            "bpm-invoice-orchestrator",
            "orchestrator",
            "Durable Functions parent for vendor invoices. Agent-scoped; approver recorded as subject.",
            app_roles={
                TOOL_GW: ("Invoices.Write", "PurchaseOrders.Read", "Workers.Read", "Incidents.Write"),
                agent_uri("ap-invoice-agent"): _AGENT_INVOKE,
                EVENT_GW: ("Events.Publish",),
            },
        ),
        AppRegistration(
            "sap-integration-suite",
            "publisher",
            "SAP middleware (Integration Suite / Event Mesh bridge) publishing canonical business events.",
            app_roles={EVENT_GW: ("Events.Publish",)},
        ),
        AppRegistration(
            "tool-gateway",
            "gateway",
            "Tool Gateway. Middle tier for Dataverse OBO pass-through.",
            obo_targets=(DATAVERSE,),
        ),
        AppRegistration(
            "mcp-gateway",
            "gateway",
            "MCP Gateway. Calls MCP servers with its own identity.",
            app_roles={MCP_SERVERS: ("McpServer.Invoke",)},
        ),
        AppRegistration(
            "event-gateway",
            "gateway",
            "Event Gateway. Validates and routes canonical business events.",
        ),
        AppRegistration(
            "a2a-gateway",
            "gateway",
            "A2A Gateway. Forwards caller tokens; holds no downstream rights of its own.",
        ),
        AppRegistration(
            "identity-gateway",
            "gateway",
            "Identity Gateway API (api://aiip-identity-gateway). Workloads present their managed-identity token for this audience; the gateway's own MI is the federated credential of every agent registration.",
        ),
    )
}

BY_URI = {r.app_id_uri: r for r in REGISTRATIONS.values()}


def by_client_id(client_id: str | None) -> AppRegistration | None:
    return next((r for r in REGISTRATIONS.values() if r.client_id == client_id), None)


def public_view() -> list[dict]:
    return [
        {
            "name": r.name,
            "kind": r.kind,
            "client_id": r.client_id,
            "app_id_uri": r.app_id_uri,
            "managed_identity": r.managed_identity,
            "obo_targets": list(r.obo_targets),
            "app_roles": {k: list(v) for k, v in r.app_roles.items()},
            "description": r.description,
        }
        for r in REGISTRATIONS.values()
    ]
