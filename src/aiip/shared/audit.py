"""Append-only, hash-chained audit log. Each record answers: who (actor + subject) did what
(operation, side effect) to which business key, when, with what result, under which trace.

Arguments are stored as a digest, not verbatim, to keep business data out of the audit store.
In Azure the same record is also emitted as a span event, landing in Application Insights."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


class AuditLog:
    def __init__(self, name: str) -> None:
        self.name = name
        self.records: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def write(self, **fields: Any) -> dict[str, Any]:
        with self._lock:
            prev = self.records[-1]["hash"] if self.records else "0" * 16
            rec = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "log": self.name, **fields}
            rec["prev_hash"] = prev
            rec["hash"] = digest({k: v for k, v in rec.items() if k != "hash"})
            self.records.append(rec)
        span = trace.get_current_span()
        if span.is_recording():
            span.add_event("audit", {k: str(v) for k, v in rec.items() if v is not None})
        sink = os.environ.get("AIIP_AUDIT_DIR")
        if sink:
            os.makedirs(sink, exist_ok=True)
            with open(os.path.join(sink, f"{self.name}.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        return rec

    def query(self, **filters: Any) -> list[dict[str, Any]]:
        return [r for r in self.records if all(r.get(k) == v for k, v in filters.items() if v is not None)]

    def verify_chain(self) -> bool:
        prev = "0" * 16
        for r in self.records:
            if r["prev_hash"] != prev or r["hash"] != digest({k: v for k, v in r.items() if k != "hash"}):
                return False
            prev = r["hash"]
        return True

    def reset(self) -> None:
        self.records.clear()
