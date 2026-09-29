"""ServiceNow connector pack (sandbox stand-in target).

auth         OAuth client credentials; the caller is recorded in caller_id (subject), the integration user as creator
idempotency  correlation_id = idempotency key; query-before-insert returns the existing incident
errors       {error:{message}} by status; 429 honours Retry-After
rate limits  X-RateLimit-Limit / X-RateLimit-Remaining"""

from __future__ import annotations

import httpx

from aiip.connectors.auth import OAuthClientCredentials
from aiip.connectors.base import (
    VALIDATION,
    ConnectorContext,
    ConnectorError,
    ConnectorPack,
    ConnectorResult,
    RateLimitHint,
    retry_after,
    send,
    status_class,
)
from aiip.connectors.canonical import Incident

TABLE = "/api/now/table/incident"


class ServiceNowPack(ConnectorPack):
    name = "servicenow"
    vendor = "servicenow"
    auth_strategy = "OAuth 2.0 client credentials (secret in Key Vault)"
    user_scoped_strategy = (
        "subject written to caller_id; Entra-federated OAuth for delegated calls in production"
    )
    idempotency_strategy = "correlation_id lookup before insert"
    rate_limit_headers = ("X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After")
    stored_fields = {
        "incident": ("sys_id", "number", "short_description", "state", "priority", "correlation_id")
    }

    def __init__(self) -> None:
        super().__init__()
        self.auth = OAuthClientCredentials(
            self.http,
            "/oauth_token.do",
            "aiip-servicenow",
            "kv://aiip-local-kv/servicenow-oauth-client-secret",
        )
        self.operations = {
            "get_incident": self.get_incident,
            "list_incidents": self.list_incidents,
            "create_incident": self.create_incident,
        }

    def hint(self, headers: httpx.Headers) -> RateLimitHint | None:
        if "x-ratelimit-remaining" not in headers:
            return None
        return RateLimitHint(
            int(headers["x-ratelimit-remaining"]),
            int(headers.get("x-ratelimit-limit", 0)) or None,
            retry_after(headers),
            "X-RateLimit-*",
        )

    def map_error(self, resp: httpx.Response) -> ConnectorError:
        try:
            msg = resp.json().get("error", {}).get("message", "")
        except Exception:
            msg = ""
        return ConnectorError(
            status_class(resp.status_code) or VALIDATION,
            msg or f"servicenow HTTP {resp.status_code}",
            retry_after=retry_after(resp.headers),
        )

    @staticmethod
    def to_incident(r: dict) -> dict:
        return Incident(
            source_system="servicenow",
            incident_id=r["sys_id"],
            number=r["number"],
            short_description=r["short_description"],
            state=r["state"],
            priority=r["priority"],
            correlation_id=r.get("correlation_id", ""),
        ).model_dump()

    async def get_incident(self, args: dict, ctx: ConnectorContext) -> ConnectorResult:
        r = await send(self, self.auth, "GET", f"{TABLE}/{args['incident_id']}", ctx)
        rec = r.json()["result"]
        return ConnectorResult(
            {**self.to_incident(rec), "description": rec.get("description", "")}, hint=self.last_hint
        )

    async def list_incidents(self, args: dict, ctx: ConnectorContext) -> ConnectorResult:
        q = f"category={args['category']}" if args.get("category") else ""
        r = await send(
            self,
            self.auth,
            "GET",
            TABLE,
            ctx,
            params={"sysparm_query": q, "sysparm_limit": args.get("limit", 10)},
        )
        return ConnectorResult(
            {"incidents": [self.to_incident(x) for x in r.json()["result"]]}, hint=self.last_hint
        )

    async def create_incident(self, args: dict, ctx: ConnectorContext) -> ConnectorResult:
        if not ctx.idempotency_key:
            raise ConnectorError(VALIDATION, "create_incident needs an idempotency key (correlation_id)")
        found = await send(
            self,
            self.auth,
            "GET",
            TABLE,
            ctx,
            params={"sysparm_query": f"correlation_id={ctx.idempotency_key}", "sysparm_limit": 1},
        )
        existing = found.json()["result"]
        if existing:
            return ConnectorResult(self.to_incident(existing[0]), hint=self.last_hint, replayed=True)
        body = {
            "short_description": args["short_description"],
            "description": args.get("description", ""),
            "priority": str(args.get("priority", 4)),
            "category": args.get("category", "integration"),
            "correlation_id": ctx.idempotency_key,
            "caller_id": ctx.principal.subject,
        }
        r = await send(self, self.auth, "POST", TABLE, ctx, json=body)
        return ConnectorResult(
            self.to_incident(r.json()["result"]), hint=self.last_hint, vendor_status=r.status_code
        )
