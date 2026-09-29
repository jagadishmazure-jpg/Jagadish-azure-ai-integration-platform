"""A small replay-based stand-in for the Durable Task runtime, so the real orchestrator generator
runs offline. It keeps an event-sourced history and re-executes the orchestrator from the top on
every wake-up, feeding recorded results back in (activities never run twice) - the same model
Durable Functions uses. Timers use a virtual clock that tests and the demo can advance."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from aiip.bpm.activities import ACTIVITIES
from aiip.bpm.orchestration import ORCHESTRATORS


class Task:
    def __init__(
        self, kind: str, name: str = "", payload: Any = None, children: list[Task] | None = None
    ) -> None:
        self.kind, self.name, self.payload, self.children = kind, name, payload, children or []
        self.result: Any = None
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _Suspend(Exception):
    pass


@dataclass
class Instance:
    instance_id: str
    name: str
    input: Any
    created: datetime
    now: datetime
    history: list[dict[str, Any]] = field(default_factory=list)
    events: dict[str, list[Any]] = field(default_factory=dict)
    status: str = "Pending"
    custom_status: Any = None
    output: Any = None
    activity_executions: int = 0
    replays: int = 0


class LocalContext:
    def __init__(self, inst: Instance) -> None:
        self._inst = inst
        self.instance_id = inst.instance_id
        self.current_utc_datetime = inst.created
        self.is_replaying = True

    def get_input(self) -> Any:
        return self._inst.input

    def call_activity(self, name: str, input_: Any = None) -> Task:
        return Task("activity", name, input_)

    def create_timer(self, fire_at: datetime) -> Task:
        return Task("timer", payload=fire_at)

    def wait_for_external_event(self, name: str) -> Task:
        return Task("event", name)

    def task_any(self, tasks: list[Task]) -> Task:
        return Task("any", children=tasks)

    def set_custom_status(self, status: Any) -> None:
        self._inst.custom_status = status


class LocalDurableRuntime:
    def __init__(self) -> None:
        self.instances: dict[str, Instance] = {}

    async def start(self, name: str, input_: Any, instance_id: str | None = None) -> str:
        now = datetime.now(UTC).replace(microsecond=0)
        iid = instance_id or uuid.uuid4().hex
        self.instances[iid] = Instance(iid, name, input_, now, now)
        await self.drive(iid)
        return iid

    async def raise_event(self, instance_id: str, name: str, data: Any) -> None:
        inst = self.instances[instance_id]
        inst.events.setdefault(name, []).append(data)
        await self.drive(instance_id)

    async def advance(self, instance_id: str, delta: timedelta) -> None:
        inst = self.instances[instance_id]
        inst.now += delta
        await self.drive(instance_id)

    async def drive(self, instance_id: str) -> None:
        inst = self.instances[instance_id]
        if inst.status in {"Completed", "Failed"}:
            return
        inst.replays += 1
        ctx = LocalContext(inst)
        gen = ORCHESTRATORS[inst.name](ctx)
        cursor, consumed = 0, {}
        value: Any = None
        inst.status = "Running"
        try:
            while True:
                task: Task = gen.send(value)
                if cursor < len(inst.history):  # replay
                    rec = inst.history[cursor]
                    value = self._replay(task, rec)
                    ctx.current_utc_datetime = datetime.fromisoformat(rec["at"])
                else:
                    ctx.is_replaying = False
                    value = await self._execute(inst, ctx, task, consumed)
                cursor += 1
        except StopIteration as stop:
            inst.status, inst.output = "Completed", stop.value
        except _Suspend:
            inst.status = "Running"
        except Exception as exc:  # orchestrator bug or unexpected activity crash
            inst.status, inst.output = "Failed", {"error": type(exc).__name__}

    @staticmethod
    def _replay(task: Task, rec: dict[str, Any]) -> Any:
        if task.kind == "activity":
            assert rec["kind"] == "activity" and rec["name"] == task.name, "non-deterministic orchestrator"
            return rec["result"]
        winner = task.children[rec["winner"]]
        winner.result = rec.get("result")
        return winner

    async def _execute(self, inst: Instance, ctx: LocalContext, task: Task, consumed: dict) -> Any:
        at = inst.now.isoformat()
        if task.kind == "activity":
            result = await ACTIVITIES[task.name](task.payload)
            inst.activity_executions += 1
            inst.history.append({"kind": "activity", "name": task.name, "result": result, "at": at})
            return result
        if task.kind == "any":
            for i, child in enumerate(task.children):
                if child.kind == "event" and inst.events.get(child.name):
                    child.result = inst.events[child.name].pop(0)
                    inst.history.append({"kind": "any", "winner": i, "result": child.result, "at": at})
                    return child
                if child.kind == "timer" and inst.now >= child.payload:
                    inst.history.append({"kind": "any", "winner": i, "result": None, "at": at})
                    return child
            raise _Suspend()
        raise _Suspend()

    def status(self, instance_id: str) -> dict[str, Any]:
        inst = self.instances[instance_id]
        return {
            "name": inst.name,
            "instanceId": inst.instance_id,
            "runtimeStatus": inst.status,
            "input": inst.input,
            "customStatus": inst.custom_status,
            "output": inst.output,
            "createdTime": inst.created.isoformat(),
            "lastUpdatedTime": inst.now.isoformat(),
            "historyEvents": len(inst.history),
            "activityExecutions": inst.activity_executions,
            "replays": inst.replays,
        }
