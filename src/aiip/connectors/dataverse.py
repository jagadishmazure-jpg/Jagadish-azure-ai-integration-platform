"""Dynamics 365 / Dataverse connector pack (sandbox stand-in target).

auth         Entra ID OBO: the tool gateway exchanges the user's token for the environment URL
user-scoped  native: Dataverse enforces the user's security roles / business unit
idempotency  PATCH upsert by alternate key (ticketnumber = idempotency key)
rate limits  x-ms-ratelimit-burst-remaining-xrm-requests, 429 + Retry-After"""

from __future__ import annotations

import httpx

from aiip.connectors.auth import EntraObo
from aiip.connectors.base import (
    VALIDATION,
    ConnectorContext,
    ConnectorError,
    ConnectorPack,
    ConnectorResult,
    RateLimitHint,
    retry_after,
    send,
)
from aiip.connectors.canonical import Entitlement
from aiip.identity.registrations import DATAVERSE

API = "/api/data/v9.2"


class DataversePack(ConnectorPack):
    name = "dataverse"
    vendor = "dataverse"
    auth_strategy = "Entra ID on-behalf-of (tool gateway as middle tier)"
    user_scoped_strategy = "native OBO: Dataverse applies the user's security roles"
    idempotency_strategy = "PATCH upsert on alternate key"
    rate_limit_headers = ("x-ms-ratelimit-burst-remaining-xrm-requests", "Retry-After")
    stored_fields = {"entitlement": ("_customerid_value", "name", "slaresponsehours")}

    def __init__(self) -> None:
        super().__init__()
        self.auth = EntraObo(DATAVERSE)
        self.operations = {"get_entitlement": self.get_entitlement, "upsert_incident": self.upsert_incident}

    def hint(self, headers: httpx.Headers) -> RateLimitHint | None:
        v = headers.get("x-ms-ratelimit-burst-remaining-xrm-requests")
        return RateLimitHint(int(v), 6000, retry_after(headers), "x-ms-ratelimit-*") if v else None

    async def get_entitlement(self, args: dict, ctx: ConnectorContext) -> ConnectorResult:
        r = await send(
            self,
            self.auth,
            "GET",
            f"{API}/entitlements",
            ctx,
            params={
                "$filter": f"_customerid_value eq '{args['account_number']}'",
                "$select": "name,slaresponsehours,_customerid_value",
            },
        )
        rows = r.json()["value"]
        if not rows:
            raise ConnectorError("authz_deny", "no entitlement visible to this user")
        e = rows[0]
        return ConnectorResult(
            Entitlement(
                source_system="dataverse",
                customer_id=e["_customerid_value"],
                name=e["name"],
                response_hours=int(e["slaresponsehours"]),
            ).model_dump(),
            hint=self.last_hint,
        )

    async def upsert_incident(self, args: dict, ctx: ConnectorContext) -> ConnectorResult:
        if not ctx.idempotency_key:
            raise ConnectorError(VALIDATION, "upsert_incident needs an idempotency key (alternate key)")
        r = await send(
            self,
            self.auth,
            "PATCH",
            f"{API}/incidents(ticketnumber='{ctx.idempotency_key}')",
            ctx,
            json={"title": args["title"], "customerid": args["account_number"]},
        )
        created = r.headers.get("x-aiip-created") == "true"
        return ConnectorResult(
            {"source_system": "dataverse", "ticketnumber": ctx.idempotency_key, "created": created},
            replayed=not created,
        )
