"""Canonical business objects. Connectors map vendor payloads *into* these and keep only the fields
the graphs need (data minimisation); anything else in the vendor record is dropped on the floor."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Canonical(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_system: str


class Customer(Canonical):
    customer_id: str
    account_number: str
    name: str
    tier: str
    region: str
    credit_hold: bool


class Case(Canonical):
    case_id: str
    external_id: str
    customer_id: str
    created: bool


class CaseSummary(Canonical):
    case_id: str
    case_number: str
    subject: str
    status: str


class SalesOrder(Canonical):
    order_id: str
    customer_ref: str
    net_amount: str
    currency: str
    status: str
    delivery_status: str
    requested_delivery_date: str


class OrderSimulation(Canonical):
    customer_ref: str
    net_amount: str
    lines: int
    simulated: bool


class Delivery(Canonical):
    delivery_id: str
    order_id: str
    planned_date: str
    revised_date: str
    carrier: str
    days_late: int


class PurchaseOrder(Canonical):
    po_id: str
    supplier_id: str
    currency: str
    cost_center: str
    total: str


class InvoiceResult(Canonical):
    invoice_document: str
    status: str
    messages: list[str]


class Incident(Canonical):
    incident_id: str
    number: str
    short_description: str
    state: str
    priority: str
    correlation_id: str


class Worker(Canonical):
    worker_id: str
    name: str
    email: str
    title: str
    cost_center: str
    manager_id: str
    is_manager: bool
    approval_limit: float


class Entitlement(Canonical):
    customer_id: str
    name: str
    response_hours: int


class Issue(Canonical):
    issue_key: str
    created: bool
