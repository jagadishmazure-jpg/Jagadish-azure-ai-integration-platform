"""Event worker. Pull a message, load the graph registered for its event type, run it under that
worker's *own* identity, emit a completion event, settle the message.

Settlement policy:
  success                                  -> complete, emit completion event, process metric
  already processed (same tenant/type/key) -> complete, no re-run (idempotent consumer)
  retryable (unavailable, timeout, rate)   -> abandon; the bus dead-letters after MaxDeliveryCount
  poison (validation, authz, not_found,
          business_reject, budget, unknown type) -> dead-letter now with a reason"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

from aiip.events.bus import BusClient, Message, bus_client
from aiip.events.canonical import make_event
from aiip.events.graphs import GRAPHS, RunContext
from aiip.identity.client import IdentityClient
from aiip.identity.registrations import EVENT_GW
from aiip.shared import errors as E
from aiip.shared import http, telemetry
from aiip.shared.tracecontext import new_traceparent

RETRYABLE = {E.UNAVAILABLE, E.TIMEOUT, E.RATE_LIMITED}


class EventWorker:
    def __init__(self, queue: str, bus: BusClient | None = None) -> None:
        self.queue = queue
        self.bus = bus or bus_client()
        self.processed: dict[str, str] = {}  # (tenant|type|key) -> run_id ; Cosmos/Table in production
        self.identities: dict[str, IdentityClient] = {}

    def _identity(self, app: str) -> IdentityClient:
        if app not in self.identities:
            self.identities[app] = IdentityClient(app)
        return self.identities[app]

    async def handle(self, msg: Message) -> dict[str, Any]:
        from aiip.config import TENANT_NAMES

        ev = msg.body
        etype, key = ev.get("type", ""), ev.get("businesskey", ev.get("subject", ""))
        tenant = TENANT_NAMES.get(ev.get("tenantid", ""), "contoso")
        dedup = f"{ev.get('tenantid')}|{etype}|{key}"
        if dedup in self.processed:
            await self.bus.complete(self.queue, msg)
            return {
                "message_id": msg.message_id,
                "status": "duplicate_skipped",
                "run_id": self.processed[dedup],
            }
        if etype not in GRAPHS:
            await self.bus.dead_letter(self.queue, msg, "UnknownEventType", etype)
            return {"message_id": msg.message_id, "status": "dead_lettered", "reason": "unknown event type"}
        app, graph = GRAPHS[etype]
        ctx = RunContext(
            event=ev,
            identity=self._identity(app),
            tenant=tenant,
            traceparent=ev.get("traceparent") or new_traceparent(),
        )
        with telemetry.integration_span(
            f"worker {etype}",
            service=f"worker-{self.queue}",
            system="service-bus",
            operation=etype.split(".")[-2],
            business_key=key,
            identity_mode="agent",
            actor=app,
            subject=app,
            tenant=ev.get("tenantid"),
            traceparent=ctx.traceparent,
            run_id=ctx.run_id,
            delivery_count=msg.delivery_count,
        ) as span:
            try:
                result = await graph(ctx)
            except E.GatewayError as exc:
                span.set_result(exc.code)
                if exc.code in RETRYABLE:
                    await self.bus.abandon(self.queue, msg)
                    return {
                        "message_id": msg.message_id,
                        "status": "abandoned",
                        "result_class": exc.code,
                        "delivery_count": msg.delivery_count,
                        "run_id": ctx.run_id,
                    }
                await self.bus.dead_letter(self.queue, msg, exc.code, exc.message)
                telemetry.record_process(etype, "dead_lettered")
                return {
                    "message_id": msg.message_id,
                    "status": "dead_lettered",
                    "result_class": exc.code,
                    "run_id": ctx.run_id,
                }
            except Exception as exc:  # unexpected: let the bus retry, then dead-letter
                span.set_result(E.INTERNAL)
                await self.bus.abandon(self.queue, msg)
                return {
                    "message_id": msg.message_id,
                    "status": "abandoned",
                    "result_class": E.INTERNAL,
                    "error": type(exc).__name__,
                }
        name, data = result["completion"]
        await self._emit(app, name, data, ev.get("tenantid", ""), ctx.traceparent)
        await self.bus.complete(self.queue, msg)
        self.processed[dedup] = ctx.run_id
        telemetry.record_process(etype, result["outcome"])
        return {
            "message_id": msg.message_id,
            "status": "completed",
            "outcome": result["outcome"],
            "run_id": ctx.run_id,
            "steps": ctx.trail,
            "completion": name,
        }

    async def _emit(self, app: str, name: str, data: dict, tenant_id: str, traceparent: str) -> None:
        from aiip.config import TENANT_NAMES

        token = await self._identity(app).agent_token(EVENT_GW, TENANT_NAMES.get(tenant_id, "contoso"))
        ev = make_event(
            name, data, tenant_id=tenant_id, source=f"/aiip/workers/{app}", traceparent=traceparent
        )
        async with http.client("event-gateway") as c:
            r = await c.post("/v1/events", json=ev, headers={"authorization": f"Bearer {token}"})
        if r.status_code != 200:
            raise E.GatewayError(E.UNAVAILABLE, "completion event rejected")

    async def run_once(self, max_messages: int = 10) -> list[dict[str, Any]]:
        out = []
        while len(out) < max_messages:
            batch = await self.bus.receive(self.queue, 1)
            if not batch:
                break
            out.append(await self.handle(batch[0]))
        return out

    async def run_forever(self, poll_s: float = 1.0) -> None:  # pragma: no cover - process loop
        while True:
            if not await self.run_once():
                await asyncio.sleep(poll_s)


def main() -> None:  # pragma: no cover
    queue = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("WORKER_QUEUE", "order-events")
    telemetry.configure(f"worker-{queue}")
    asyncio.run(EventWorker(queue).run_forever())


if __name__ == "__main__":  # pragma: no cover
    main()
