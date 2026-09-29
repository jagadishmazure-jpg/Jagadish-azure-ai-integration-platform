"""Rate limiting, short-TTL caching and circuit breaking. Clocks are injectable so failure drills
are deterministic in tests."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

Clock = Callable[[], float]


@dataclass
class TokenBucket:
    capacity: float
    refill_per_s: float
    clock: Clock = time.monotonic
    _state: dict[str, tuple[float, float]] = field(default_factory=dict)

    def try_acquire(self, key: str, cost: float = 1.0) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        now = self.clock()
        tokens, last = self._state.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill_per_s)
        if tokens >= cost:
            self._state[key] = (tokens - cost, now)
            return True, 0.0
        self._state[key] = (tokens, now)
        return False, (cost - tokens) / self.refill_per_s if self.refill_per_s else 60.0

    def reset(self) -> None:
        self._state.clear()


@dataclass
class TTLCache:
    ttl_s: float
    clock: Clock = time.monotonic
    max_items: int = 2048
    _data: dict[str, tuple[float, Any]] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item and item[0] > self.clock():
            self.hits += 1
            return item[1]
        self._data.pop(key, None)
        self.misses += 1
        return None

    def set(self, key: str, value: Any, ttl_s: float | None = None) -> None:
        if len(self._data) >= self.max_items:
            self._data.pop(next(iter(self._data)))
        self._data[key] = (self.clock() + (ttl_s if ttl_s is not None else self.ttl_s), value)

    def reset(self) -> None:
        self._data.clear()
        self.hits = self.misses = 0


class CircuitOpen(Exception):
    def __init__(self, name: str, retry_after: float) -> None:
        super().__init__(f"circuit {name} open")
        self.name, self.retry_after = name, retry_after


@dataclass
class CircuitBreaker:
    """closed -> (N consecutive failures) -> open -> (reset timeout) -> half_open -> one probe."""

    name: str
    failure_threshold: int = 3
    reset_timeout_s: float = 30.0
    clock: Clock = time.monotonic
    state: str = "closed"
    failures: int = 0
    opened_at: float = 0.0
    transitions: list[str] = field(default_factory=list)

    def _move(self, state: str) -> None:
        if state != self.state:
            self.transitions.append(f"{self.state}->{state}")
            self.state = state

    def before_call(self) -> None:
        if self.state == "open":
            waited = self.clock() - self.opened_at
            if waited < self.reset_timeout_s:
                raise CircuitOpen(self.name, self.reset_timeout_s - waited)
            self._move("half_open")

    def record_success(self) -> None:
        self.failures = 0
        self._move("closed")

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == "half_open" or self.failures >= self.failure_threshold:
            self.opened_at = self.clock()
            self._move("open")
