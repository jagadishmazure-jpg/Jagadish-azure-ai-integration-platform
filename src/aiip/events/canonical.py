"""Canonical business events (CloudEvents 1.0, structured JSON). Each has a stable business key
carried as the `subject` and the `businesskey` extension; tenant and traceparent ride along."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class EventClass:
    name: str
    type: str
    queue: str
    business_key: str
    schema: dict[str, Any]
    budget_per_minute: int  # admission budget per tenant: protects against event storms


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


EVENT_CLASSES: dict[str, EventClass] = {
    e.type: e
    for e in (
        EventClass(
            "OrderCreated",
            "com.contoso.sap.salesorder.created.v1",
            "order-events",
            "order_id",
            _obj(
                {
                    "order_id": {"type": "string", "pattern": "^[0-9]{7}$"},
                    "customer_ref": {"type": "string"},
                    "net_amount": {"type": "string"},
                    "currency": {"type": "string"},
                },
                ["order_id", "customer_ref"],
            ),
            20,
        ),
        EventClass(
            "ShipmentLate",
            "com.contoso.sap.delivery.late.v1",
            "shipment-events",
            "delivery_id",
            _obj(
                {
                    "delivery_id": {"type": "string", "pattern": "^8[0-9]{7}$"},
                    "order_id": {"type": "string"},
                    "days_late": {"type": "integer", "minimum": 1},
                },
                ["delivery_id", "order_id", "days_late"],
            ),
            5,
        ),
        EventClass(
            "OrderTriaged",
            "com.contoso.aiip.order.triaged.v1",
            "completions",
            "order_id",
            _obj(
                {
                    "order_id": {"type": "string"},
                    "outcome": {"type": "string"},
                    "run_id": {"type": "string"},
                    "incident": {"type": ["string", "null"]},
                },
                ["order_id", "outcome", "run_id"],
            ),
            100,
        ),
        EventClass(
            "ShipmentLateHandled",
            "com.contoso.aiip.delivery.late.handled.v1",
            "completions",
            "delivery_id",
            _obj(
                {
                    "delivery_id": {"type": "string"},
                    "outcome": {"type": "string"},
                    "run_id": {"type": "string"},
                    "case_id": {"type": ["string", "null"]},
                },
                ["delivery_id", "outcome", "run_id"],
            ),
            100,
        ),
    )
}
BY_NAME = {e.name: e for e in EVENT_CLASSES.values()}

ENVELOPE_SCHEMA = {
    "type": "object",
    "required": ["specversion", "id", "source", "type", "subject", "data", "tenantid"],
    "properties": {
        "specversion": {"const": "1.0"},
        "id": {"type": "string", "minLength": 1, "maxLength": 128},
        "source": {"type": "string", "minLength": 1},
        "type": {"type": "string"},
        "subject": {"type": "string", "minLength": 1},
        "time": {"type": "string"},
        "datacontenttype": {"const": "application/json"},
        "tenantid": {"type": "string"},
        "traceparent": {"type": "string"},
        "businesskey": {"type": "string"},
        "data": {"type": "object"},
    },
}


def make_event(
    name: str,
    data: dict[str, Any],
    *,
    tenant_id: str,
    source: str = "/sap/s4/contoso-prd",
    traceparent: str | None = None,
    event_id: str | None = None,
) -> dict[str, Any]:
    ec = BY_NAME[name]
    key = str(data[ec.business_key])
    ev = {
        "specversion": "1.0",
        "id": event_id or uuid.uuid4().hex,
        "source": source,
        "type": ec.type,
        "subject": key,
        "time": datetime.now(UTC).isoformat(timespec="seconds"),
        "datacontenttype": "application/json",
        "tenantid": tenant_id,
        "businesskey": key,
        "data": data,
    }
    if traceparent:
        ev["traceparent"] = traceparent
    return ev
