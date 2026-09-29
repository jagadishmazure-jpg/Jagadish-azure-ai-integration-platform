"""W3C trace-context helpers (traceparent) used on every hop, including A2A and bus messages."""

from __future__ import annotations

import re
import secrets

_TP = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


def new_traceparent() -> str:
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


def child_traceparent(parent: str | None) -> str:
    m = _TP.match(parent or "")
    if not m:
        return new_traceparent()
    return f"00-{m.group(1)}-{secrets.token_hex(8)}-{m.group(3)}"


def trace_id(tp: str | None) -> str:
    m = _TP.match(tp or "")
    return m.group(1) if m else ""


def valid(tp: str | None) -> bool:
    return bool(_TP.match(tp or ""))
