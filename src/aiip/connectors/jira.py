"""Jira Cloud connector pack (sandbox stand-in target).

auth         service account + API token (Key Vault) over basic auth
idempotency  label `aiip-idem-<key>`; JQL search before create
rate limits  X-RateLimit-Remaining, 429 + Retry-After"""

from __future__ import annotations

import re

import httpx

from aiip.connectors.auth import BasicApiToken
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
from aiip.connectors.canonical import Issue


class JiraPack(ConnectorPack):
    name = "jira"
    vendor = "jira"
    auth_strategy = "basic auth: service account + API token (Key Vault)"
    user_scoped_strategy = (
        "OAuth 2.0 (3LO) per user in production; sandbox uses a service account and logs the subject"
    )
    idempotency_strategy = "idempotency label + JQL lookup before create"
    rate_limit_headers = ("X-RateLimit-Limit", "X-RateLimit-Remaining", "Retry-After")
    stored_fields = {"issue": ("key",)}

    def __init__(self) -> None:
        super().__init__()
        self.auth = BasicApiToken("aiip-integration@contoso.example", "kv://aiip-local-kv/jira-api-token")
        self.operations = {"create_issue": self.create_issue}

    def hint(self, headers: httpx.Headers) -> RateLimitHint | None:
        v = headers.get("x-ratelimit-remaining")
        return (
            RateLimitHint(
                int(v),
                int(headers.get("x-ratelimit-limit", 0)) or None,
                retry_after(headers),
                "X-RateLimit-*",
            )
            if v
            else None
        )

    def map_error(self, resp: httpx.Response) -> ConnectorError:
        try:
            body = resp.json()
            msg = "; ".join(
                body.get("errorMessages", []) + [f"{k}: {v}" for k, v in body.get("errors", {}).items()]
            )
        except Exception:
            msg = ""
        return ConnectorError(
            status_class(resp.status_code) or VALIDATION,
            msg or f"jira HTTP {resp.status_code}",
            retry_after=retry_after(resp.headers),
        )

    async def create_issue(self, args: dict, ctx: ConnectorContext) -> ConnectorResult:
        if not ctx.idempotency_key or not re.match(r"^[\w\-:.]{4,80}$", ctx.idempotency_key):
            raise ConnectorError(VALIDATION, "create_issue needs a label-safe idempotency key")
        label = f"aiip-idem-{ctx.idempotency_key}"
        found = await send(
            self,
            self.auth,
            "GET",
            "/rest/api/3/search/jql",
            ctx,
            params={"jql": f'labels = "{label}"', "fields": "key"},
        )
        issues = found.json().get("issues", [])
        if issues:
            return ConnectorResult(
                Issue(source_system="jira", issue_key=issues[0]["key"], created=False).model_dump(),
                replayed=True,
                hint=self.last_hint,
            )
        body = {
            "fields": {
                "project": {"key": args["project"]},
                "summary": args["summary"],
                "issuetype": {"name": args.get("issue_type", "Task")},
                "labels": [label, "aiip"],
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": args.get("description", "")}],
                        }
                    ],
                },
            }
        }
        r = await send(self, self.auth, "POST", "/rest/api/3/issue", ctx, json=body)
        return ConnectorResult(
            Issue(source_system="jira", issue_key=r.json()["key"], created=True).model_dump(),
            hint=self.last_hint,
            vendor_status=r.status_code,
        )
