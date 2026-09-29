"""Mutable sandbox state: fixture data, issued vendor tokens, injected faults, call counters."""

from __future__ import annotations

import asyncio
import copy
import json
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

FIXTURES = Path(__file__).parent / "fixtures"
DATA: dict[str, Any] = {}
TOKENS: dict[str, dict[str, Any]] = {}
FAULTS: dict[str, dict[str, Any]] = {}
CALLS: dict[str, int] = {}
WAREHOUSE: sqlite3.Connection | None = None


def reset() -> None:
    global WAREHOUSE
    DATA.clear()
    for name in ("salesforce", "sap", "servicenow", "workday", "dataverse", "jira"):
        DATA[name] = copy.deepcopy(json.loads((FIXTURES / f"{name}.json").read_text()))
    DATA["sap"].update({"invoices": {}, "idempotency": {}, "csrf": set()})
    TOKENS.clear()
    FAULTS.clear()
    CALLS.clear()
    WAREHOUSE = sqlite3.connect(":memory:", check_same_thread=False)
    WAREHOUSE.executescript((FIXTURES / "warehouse.sql").read_text())
    WAREHOUSE.execute("PRAGMA query_only = ON")  # defense in depth: the warehouse stand-in is read-only


def issue_token(vendor: str, user: str, **extra: Any) -> str:
    token = f"{vendor}-{secrets.token_urlsafe(24)}"
    TOKENS[token] = {"vendor": vendor, "user": user, "exp": time.time() + 3600, **extra}
    return token


def token_info(vendor: str, authorization: str | None) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, detail="missing bearer token")
    info = TOKENS.get(authorization.split(" ", 1)[1])
    if not info or info["vendor"] != vendor or info["exp"] < time.time():
        raise HTTPException(401, detail="invalid or expired token")
    return info


async def apply_fault(vendor: str) -> None:
    CALLS[vendor] = CALLS.get(vendor, 0) + 1
    f = FAULTS.get(vendor)
    if not f or f.get("remaining", 0) <= 0:
        return
    f["remaining"] -= 1
    if f["mode"] == "slow":
        await asyncio.sleep(float(f.get("delay_s", 2.0)))
    elif f["mode"] == "error":
        raise HTTPException(503, detail="service unavailable (injected fault)")
    elif f["mode"] == "rate_limit":
        raise HTTPException(429, detail="rate limited (injected fault)", headers={"Retry-After": "2"})


reset()
