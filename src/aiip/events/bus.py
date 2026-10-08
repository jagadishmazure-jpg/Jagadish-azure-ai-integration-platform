"""Bus abstraction with three implementations:

* InMemoryBus        - Service Bus stand-in hosted by the Event Gateway in local mode (peek-lock,
                       delivery count, MaxDeliveryCount -> dead-letter queue, parked queue).
* HttpBusClient      - worker-side client for the stand-in over HTTP.
* ServiceBusClientBus / EventGridPublisher - the Azure code paths (azure-servicebus, azure-eventgrid)."""

from __future__ import annotations

import os
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Protocol

from aiip.shared import errors as E
from aiip.shared import http


@dataclass
class Message:
    message_id: str
    body: dict[str, Any]
    properties: dict[str, str] = field(default_factory=dict)
    delivery_count: int = 0
    lock_token: str = ""
    locked_until: float = 0.0
    dead_letter_reason: str = ""
    handle: Any = None  # SDK message object in Azure mode

    def public(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "body": self.body,
            "properties": self.properties,
            "delivery_count": self.delivery_count,
            "lock_token": self.lock_token,
            "dead_letter_reason": self.dead_letter_reason,
        }


class BusClient(Protocol):
    async def receive(self, queue: str, max_messages: int = 1) -> list[Message]:
        """Lock and return up to ``max_messages`` messages."""

    async def complete(self, queue: str, msg: Message) -> None:
        """Settle ``msg`` as done."""

    async def abandon(self, queue: str, msg: Message) -> None:
        """Release the lock so ``msg`` is delivered again."""

    async def dead_letter(self, queue: str, msg: Message, reason: str, description: str) -> None:
        """Move ``msg`` to the dead-letter queue with a reason."""


class InMemoryBus:
    def __init__(self, max_delivery_count: int = 3, lock_s: float = 30.0) -> None:
        self.max_delivery_count, self.lock_s = max_delivery_count, lock_s
        self.active: dict[str, deque[Message]] = {}
        self.locked: dict[str, dict[str, Message]] = {}
        self.dlq: dict[str, list[Message]] = {}
        self.parked: dict[str, list[Message]] = {}

    def send(self, queue: str, msg: Message) -> None:
        self.active.setdefault(queue, deque()).append(msg)

    def park(self, queue: str, msg: Message, reason: str) -> None:
        msg.dead_letter_reason = reason
        self.parked.setdefault(queue, []).append(msg)

    def _expire_locks(self, queue: str) -> None:
        now = time.monotonic()
        for tok, m in list(self.locked.get(queue, {}).items()):
            if m.locked_until < now:
                del self.locked[queue][tok]
                self._requeue(queue, m)

    def _requeue(self, queue: str, m: Message) -> None:
        if m.delivery_count >= self.max_delivery_count:
            m.dead_letter_reason = "MaxDeliveryCountExceeded"
            self.dlq.setdefault(queue, []).append(m)
        else:
            self.active.setdefault(queue, deque()).appendleft(m)

    def receive(self, queue: str, max_messages: int = 1) -> list[Message]:
        self._expire_locks(queue)
        out = []
        q = self.active.setdefault(queue, deque())
        while q and len(out) < max_messages:
            m = q.popleft()
            m.delivery_count += 1
            m.lock_token = secrets.token_hex(8)
            m.locked_until = time.monotonic() + self.lock_s
            self.locked.setdefault(queue, {})[m.lock_token] = m
            out.append(m)
        return out

    def _take(self, queue: str, lock_token: str) -> Message:
        m = self.locked.get(queue, {}).pop(lock_token, None)
        if m is None:
            raise E.GatewayError(E.CONFLICT, "message lock lost")
        return m

    def complete(self, queue: str, lock_token: str) -> None:
        self._take(queue, lock_token)

    def abandon(self, queue: str, lock_token: str) -> None:
        self._requeue(queue, self._take(queue, lock_token))

    def dead_letter(self, queue: str, lock_token: str, reason: str, description: str = "") -> None:
        m = self._take(queue, lock_token)
        m.dead_letter_reason = f"{reason}: {description}"[:200]
        self.dlq.setdefault(queue, []).append(m)

    def stats(self) -> dict[str, dict[str, int]]:
        names = set(self.active) | set(self.dlq) | set(self.parked) | set(self.locked)
        return {
            q: {
                "active": len(self.active.get(q, [])),
                "locked": len(self.locked.get(q, {})),
                "dead_letter": len(self.dlq.get(q, [])),
                "parked": len(self.parked.get(q, [])),
            }
            for q in sorted(names)
        }

    def depths(self) -> dict[str, int]:
        out = {}
        for q, s in self.stats().items():
            out[q] = s["active"]
            out[f"{q}/$deadletterqueue"] = s["dead_letter"]
            out[f"{q}/parked"] = s["parked"]
        return out

    def reset(self) -> None:
        self.active.clear()
        self.locked.clear()
        self.dlq.clear()
        self.parked.clear()


class HttpBusClient:
    """Worker-side client for the local stand-in exposed by the Event Gateway under /bus."""

    async def _post(self, path: str, body: dict | None = None) -> Any:
        async with http.client("event-gateway") as c:
            r = await c.post(path, json=body or {})
        if r.status_code != 200:
            raise E.GatewayError(E.CONFLICT, f"bus call failed: HTTP {r.status_code}")
        return r.json()

    async def receive(self, queue: str, max_messages: int = 1) -> list[Message]:
        data = await self._post(f"/bus/{queue}/receive?max_messages={max_messages}")
        return [Message(**{k: v for k, v in m.items()}) for m in data["messages"]]

    async def complete(self, queue: str, msg: Message) -> None:
        await self._post(f"/bus/{queue}/complete", {"lock_token": msg.lock_token})

    async def abandon(self, queue: str, msg: Message) -> None:
        await self._post(f"/bus/{queue}/abandon", {"lock_token": msg.lock_token})

    async def dead_letter(self, queue: str, msg: Message, reason: str, description: str) -> None:
        await self._post(
            f"/bus/{queue}/deadletter",
            {"lock_token": msg.lock_token, "reason": reason, "description": description},
        )


def _text(v: Any) -> str:
    return v.decode() if isinstance(v, bytes) else str(v)


class ServiceBusClientBus:  # pragma: no cover - needs a Service Bus namespace
    """azure-servicebus 7.14 async receiver in PEEK_LOCK mode with the worker's managed identity."""

    def __init__(self) -> None:
        from azure.identity.aio import DefaultAzureCredential
        from azure.servicebus.aio import ServiceBusClient

        self._client = ServiceBusClient(os.environ["AIIP_SERVICEBUS_NAMESPACE"], DefaultAzureCredential())
        self._receivers: dict[str, Any] = {}

    def _receiver(self, queue: str):
        if queue not in self._receivers:
            self._receivers[queue] = self._client.get_queue_receiver(queue, max_wait_time=5)
        return self._receivers[queue]

    async def receive(self, queue: str, max_messages: int = 1) -> list[Message]:
        import json

        msgs = await self._receiver(queue).receive_messages(max_message_count=max_messages, max_wait_time=5)
        out = []
        for m in msgs:
            body = json.loads(b"".join(m.body))
            if (
                isinstance(body, dict)
                and "data" in body
                and "specversion" not in body
                and "eventType" in body
            ):
                body = body["data"]  # Event Grid schema wrapper, if the subscription is not CloudEvents
            props = {
                _text(k): _text(v) for k, v in (m.application_properties or {}).items()
            }  # SDK keys arrive as bytes
            out.append(Message(m.message_id, body, props, m.delivery_count or 0, str(m.lock_token), handle=m))
        return out

    async def complete(self, queue: str, msg: Message) -> None:
        await self._receiver(queue).complete_message(msg.handle)

    async def abandon(self, queue: str, msg: Message) -> None:
        await self._receiver(queue).abandon_message(msg.handle)

    async def dead_letter(self, queue: str, msg: Message, reason: str, description: str) -> None:
        await self._receiver(queue).dead_letter_message(
            msg.handle, reason=reason, error_description=description[:1000]
        )


class EventGridPublisher:  # pragma: no cover - needs an Event Grid topic
    """azure-eventgrid 4.22: publish canonical CloudEvents to the custom topic with managed identity."""

    def __init__(self) -> None:
        from azure.eventgrid.aio import EventGridPublisherClient
        from azure.identity.aio import DefaultAzureCredential

        self._client = EventGridPublisherClient(
            os.environ["AIIP_EVENTGRID_TOPIC_ENDPOINT"], DefaultAzureCredential()
        )

    async def publish(self, event: dict[str, Any]) -> None:
        from azure.core.messaging import CloudEvent

        ext = {k: event[k] for k in ("tenantid", "businesskey", "traceparent") if k in event}
        await self._client.send(
            [
                CloudEvent(
                    source=event["source"],
                    type=event["type"],
                    data=event["data"],
                    subject=event["subject"],
                    id=event["id"],
                    extensions=ext,
                )
            ]
        )


def bus_client() -> BusClient:
    from aiip.config import is_azure

    return ServiceBusClientBus() if is_azure() else HttpBusClient()
