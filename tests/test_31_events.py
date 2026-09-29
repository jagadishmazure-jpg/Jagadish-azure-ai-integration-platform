"""Section 31 - Event-driven agents: canonical events, admission (schema, tenant, dedup, budget),
worker with agent identity, completion events, retry -> dead-letter, poison messages, and the
real azure-servicebus / azure-eventgrid code paths exercised against fakes."""

from __future__ import annotations

import json

import pytest
from tests.conftest import agent_token, call, fault

from aiip.config import TENANTS
from aiip.events import gateway as ev_gw
from aiip.events.bus import InMemoryBus, Message
from aiip.events.canonical import BY_NAME, EVENT_CLASSES, make_event
from aiip.events.worker import EventWorker
from aiip.identity.registrations import EVENT_GW
from aiip.tools import gateway as tool_gw

TID = TENANTS["contoso"]


async def publish(events, app: str = "sap-integration-suite", tenant: str = "contoso"):
    tok = await agent_token(app, EVENT_GW, tenant)
    return await call("event-gateway", "POST", "/v1/events", tok, json=events)


def order_created(order="4500003", customer="ACC-1003", **kw):
    return make_event("OrderCreated", {"order_id": order, "customer_ref": customer}, tenant_id=TID, **kw)


def shipment_late(delivery="80000001", order="4500001", days=7, tenant_id=TID):
    return make_event(
        "ShipmentLate", {"delivery_id": delivery, "order_id": order, "days_late": days}, tenant_id=tenant_id
    )


def test_canonical_event_catalog():
    assert {e.name for e in EVENT_CLASSES.values()} == {
        "OrderCreated",
        "ShipmentLate",
        "OrderTriaged",
        "ShipmentLateHandled",
    }
    ev = order_created()
    assert ev["specversion"] == "1.0" and ev["subject"] == ev["businesskey"] == "4500003"
    assert ev["type"] == BY_NAME["OrderCreated"].type


async def test_admission_validates_schema_and_tenant():
    bad = order_created()
    bad["data"]["order_id"] = "12"
    s, body = await publish([bad])
    assert s == 400 and body["error"]["code"] == "validation_error"
    s, body = await publish([shipment_late(tenant_id=TENANTS["fabrikam"])])
    assert s == 403  # contoso's publisher cannot publish fabrikam events


async def test_publish_requires_events_publish_role():
    tok = await agent_token("data-agent", "api://aiip-mcp-gateway")
    s, _ = await call("event-gateway", "POST", "/v1/events", tok, json=[order_created()])
    assert s == 401  # wrong audience entirely


async def test_dedup_on_business_key():
    _s, body = await publish([order_created(), order_created()])
    assert [r["status"] for r in body["results"]] == ["accepted", "duplicate"]


async def test_event_class_budget_parks_a_storm():
    storm = [shipment_late(delivery=f"8900{i:04d}") for i in range(7)]
    _, body = await publish(storm)
    statuses = [r["status"] for r in body["results"]]
    assert statuses.count("accepted") == BY_NAME["ShipmentLate"].budget_per_minute
    assert statuses.count("parked_budget") == 2
    assert ev_gw.BUS.stats()["shipment-events"]["parked"] == 2


async def test_worker_runs_graph_with_agent_identity_and_emits_completion():
    await publish([order_created()])
    out = await EventWorker("order-events").run_once()
    assert out[0]["status"] == "completed" and out[0]["outcome"] == "blocked_credit_hold"
    rows = [r for r in tool_gw.AUDIT.records if r["operation"] == "itsm.create_incident"]
    assert rows and rows[0]["actor"] == "worker-order-events" and rows[0]["identity_mode"] == "agent"
    completions = ev_gw.BUS.active["completions"]
    assert completions[0].body["type"] == BY_NAME["OrderTriaged"].type
    assert completions[0].body["data"]["run_id"] == out[0]["run_id"]


async def test_redelivered_message_is_not_processed_twice():
    await publish([order_created()])
    worker = EventWorker("order-events")
    await worker.run_once()
    # same business event arrives again (for example, the gateway dedup window has passed)
    ev_gw.BUS.send("order-events", Message("again", order_created(), {}))
    out = await worker.run_once()
    assert out[0]["status"] == "duplicate_skipped"


async def test_transient_failures_are_retried_then_dead_lettered():
    await publish([shipment_late()])
    await fault("sap", "error", count=100)
    worker = EventWorker("shipment-events")
    statuses = []
    for _ in range(3):
        tool_gw.BREAKERS.clear()  # keep the breaker closed so each delivery really reaches SAP
        statuses += [x["status"] for x in await worker.run_once()]
    assert statuses == ["abandoned", "abandoned", "abandoned"]
    dlq = ev_gw.BUS.dlq["shipment-events"]
    assert dlq[0].dead_letter_reason == "MaxDeliveryCountExceeded" and dlq[0].delivery_count == 3


async def test_poison_message_is_dead_lettered_immediately_with_reason():
    await publish([shipment_late(delivery="89999999", order="4599999", days=2)])
    out = await EventWorker("shipment-events").run_once()
    assert out[0]["status"] == "dead_lettered" and out[0]["result_class"] == "not_found"
    _s, body = await call("event-gateway", "GET", "/bus/shipment-events/deadletter")
    assert body["messages"][0]["dead_letter_reason"].startswith("not_found")


async def test_unknown_event_type_goes_to_dead_letter():
    ev_gw.BUS.send(
        "order-events", Message("x", {"type": "com.contoso.unknown.v1", "subject": "1", "tenantid": TID}, {})
    )
    out = await EventWorker("order-events").run_once()
    assert out[0]["status"] == "dead_lettered"


async def test_graph_step_budget():
    from aiip.events.graphs import RunContext
    from aiip.identity.client import IdentityClient
    from aiip.shared.errors import GatewayError

    ctx = RunContext(
        event={},
        identity=IdentityClient("worker-order-events"),
        tenant="contoso",
        traceparent="00-" + "1" * 32 + "-" + "2" * 16 + "-01",
        max_steps=2,
    )
    ctx.step("a")
    ctx.step("b")
    with pytest.raises(GatewayError) as e:
        ctx.step("c")
    assert e.value.code == "budget_exceeded"


async def test_event_grid_webhook_validation_handshake():
    s, _ = await call(
        "event-gateway", "OPTIONS", "/v1/events", headers={"webhook-request-origin": "eventgrid.azure.net"}
    )
    assert s == 200


def test_in_memory_bus_lock_and_delivery_semantics():
    bus = InMemoryBus(max_delivery_count=2)
    bus.send("q", Message("m1", {"a": 1}))
    m = bus.receive("q")[0]
    bus.abandon("q", m.lock_token)
    m = bus.receive("q")[0]
    assert m.delivery_count == 2
    bus.abandon("q", m.lock_token)
    assert bus.stats()["q"]["dead_letter"] == 1 and bus.receive("q") == []


# ------------------------------------------------------------------ real SDK code paths (faked I/O)
class _FakeReceived:
    def __init__(self, body: dict, n: int = 1):
        self.body = iter([json.dumps(body).encode()])
        self.message_id, self.delivery_count, self.lock_token = "sb-1", n, "lock-1"
        self.application_properties = {b"tenant": b"contoso"}


class _FakeReceiver:
    def __init__(self):
        self.calls = []

    async def receive_messages(self, max_message_count, max_wait_time):
        return [_FakeReceived(order_created())]

    async def complete_message(self, m):
        self.calls.append(("complete", m.message_id))

    async def abandon_message(self, m):
        self.calls.append(("abandon", m.message_id))

    async def dead_letter_message(self, m, reason, error_description):
        self.calls.append(("dead_letter", reason, error_description))


async def test_service_bus_adapter_uses_peek_lock_settlement(monkeypatch):
    import azure.identity.aio as idaio
    import azure.servicebus.aio as sbaio

    from aiip.events.bus import ServiceBusClientBus

    receiver = _FakeReceiver()

    class FakeClient:
        def __init__(self, ns, cred):
            self.ns = ns

        def get_queue_receiver(self, queue, max_wait_time):
            return receiver

    monkeypatch.setattr(sbaio, "ServiceBusClient", FakeClient)
    monkeypatch.setattr(idaio, "DefaultAzureCredential", lambda: object())
    monkeypatch.setenv("AIIP_SERVICEBUS_NAMESPACE", "aiip-sb.servicebus.windows.net")
    bus = ServiceBusClientBus()
    msgs = await bus.receive("order-events")
    assert msgs[0].body["type"] == BY_NAME["OrderCreated"].type and msgs[0].properties["tenant"] == "contoso"
    await bus.complete("order-events", msgs[0])
    await bus.dead_letter("order-events", msgs[0], "not_found", "x" * 2000)
    assert receiver.calls[0] == ("complete", "sb-1")
    assert receiver.calls[1][0] == "dead_letter" and len(receiver.calls[1][2]) == 1000


async def test_event_grid_adapter_publishes_cloudevents_with_extensions(monkeypatch):
    import azure.eventgrid.aio as egaio
    import azure.identity.aio as idaio

    from aiip.events.bus import EventGridPublisher

    sent = []

    class FakeEG:
        def __init__(self, endpoint, cred):
            self.endpoint = endpoint

        async def send(self, events):
            sent.extend(events)

    monkeypatch.setattr(egaio, "EventGridPublisherClient", FakeEG)
    monkeypatch.setattr(idaio, "DefaultAzureCredential", lambda: object())
    monkeypatch.setenv(
        "AIIP_EVENTGRID_TOPIC_ENDPOINT", "https://aiip.eastus-1.eventgrid.azure.net/api/events"
    )
    ev = order_created(traceparent="00-" + "c" * 32 + "-" + "d" * 16 + "-01")
    await EventGridPublisher().publish(ev)
    ce = sent[0]
    assert ce.type == ev["type"] and ce.subject == "4500003"
    assert ce.extensions["tenantid"] == TID and ce.extensions["businesskey"] == "4500003"
