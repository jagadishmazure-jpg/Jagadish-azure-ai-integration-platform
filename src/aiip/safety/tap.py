"""Egress tap: a mirror of every outbound payload the supervisor is asked to send (tool
arguments, sandbox inputs, model prompts, outbound messages), taken *before* the policy decision
so blocked attempts are visible too.

The audit chain deliberately stores only argument digests. The tap is the one place payload
copies exist: bounded (oldest entries fall off), in memory, readable only by the out-of-band
monitor, and never returned to an agent. It plays the role a port mirror plays for a network
watchdog. In Azure the equivalent would be a short-retention, monitor-only Event Hub."""

from __future__ import annotations

import copy
import itertools
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any


class EgressTap:
    def __init__(self, capacity: int = 5000) -> None:
        self._buf: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._seq = itertools.count(1)
        self._lock = threading.Lock()
        self._listeners: list[Callable[[], None]] = []

    def mirror(self, *, tenant: str, actor: str, session: str, action: str, target: str, payload: Any) -> int:
        with self._lock:
            seq = next(self._seq)
            self._buf.append(
                {
                    "seq": seq,
                    "t_mono_ns": time.monotonic_ns(),
                    "tenant": tenant,
                    "actor": actor,
                    "session": session,
                    "action": action,
                    "target": target,
                    "payload": copy.deepcopy(payload),
                }
            )
        for wake in list(self._listeners):
            wake()
        return seq

    def read_after(self, seq: int) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._buf if e["seq"] > seq]

    def subscribe(self, wake: Callable[[], None]) -> None:
        self._listeners.append(wake)

    def unsubscribe(self, wake: Callable[[], None]) -> None:
        if wake in self._listeners:
            self._listeners.remove(wake)

    def reset(self) -> None:
        with self._lock:
            self._buf.clear()


TAP = EgressTap()
