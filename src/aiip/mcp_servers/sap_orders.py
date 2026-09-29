"""MCP server over the SAP sales-order OData API (sandbox stand-in), reusing the SAP connector pack.
Reads are annotated read-only; the single write is idempotent and flagged for HITL at the gateway."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from aiip.connectors.base import ConnectorError
from aiip.connectors.sap_odata import SapODataPack
from aiip.mcp_servers._common import fail, workload

server = MCPServer(
    "sap-orders",
    instructions="SAP sales orders and deliveries (sandbox stand-in). Read-only unless the gateway approves a write.",
)
_pack = SapODataPack()
READ = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)


class OrderView(BaseModel):
    order_id: str = ""
    customer_ref: str = ""
    net_amount: str = ""
    currency: str = ""
    status: str = ""
    delivery_status: str = ""
    requested_delivery_date: str = ""
    error: str = ""
    message: str = ""


class DeliveryView(BaseModel):
    delivery_id: str = ""
    order_id: str = ""
    planned_date: str = ""
    revised_date: str = ""
    carrier: str = ""
    days_late: int = 0
    error: str = ""
    message: str = ""


class OrderItem(BaseModel):
    material: str
    quantity: int


@server.tool(annotations=READ)
async def get_sales_order(order_id: str) -> OrderView:
    """Sales order header (status in business terms)."""
    try:
        d = (await _pack.invoke("get_sales_order", {"order_id": order_id}, workload("sap-orders"))).data
    except ConnectorError as exc:
        return OrderView(**fail(exc))
    return OrderView(**{k: v for k, v in d.items() if k in OrderView.model_fields})


@server.tool(annotations=READ)
async def get_delivery(delivery_id: str) -> DeliveryView:
    """Outbound delivery with planned vs revised date and days late."""
    try:
        d = (await _pack.invoke("get_delivery", {"delivery_id": delivery_id}, workload("sap-orders"))).data
    except ConnectorError as exc:
        return DeliveryView(**fail(exc))
    return DeliveryView(**{k: v for k, v in d.items() if k in DeliveryView.model_fields})


@server.tool(annotations=WRITE)
async def create_sales_order(customer_ref: str, items: list[OrderItem], idempotency_key: str) -> OrderView:
    """Create a sales order. Same idempotency_key -> same order (Repeatability-Request-ID)."""
    ctx = workload("sap-orders")
    ctx.idempotency_key = idempotency_key
    try:
        d = (
            await _pack.invoke(
                "create_sales_order",
                {"customer_ref": customer_ref, "items": [i.model_dump() for i in items]},
                ctx,
            )
        ).data
    except ConnectorError as exc:
        return OrderView(**fail(exc))
    return OrderView(**{k: v for k, v in d.items() if k in OrderView.model_fields})
