"""Salesforce connector pack (sandbox stand-in target).

auth         OAuth 2.0 JWT bearer; user-scoped = federated user, agent-scoped = per-agent integration user
idempotency  upsert on the External_Id__c external-id field (PATCH is naturally idempotent)
errors       [{errorCode, message}] arrays -> taxonomy; upsert `success:false` -> business_reject
rate limits  Sforce-Limit-Info: api-usage=used/limit"""

from __future__ import annotations

import re

import httpx

from aiip.connectors.auth import SalesforceJwtBearer
from aiip.connectors.base import (
    AUTH_EXPIRED,
    AUTHZ,
    BUSINESS_REJECT,
    CONFLICT,
    NOT_FOUND,
    RATE_LIMITED,
    TRANSIENT,
    VALIDATION,
    ConnectorContext,
    ConnectorError,
    ConnectorPack,
    ConnectorResult,
    RateLimitHint,
    retry_after,
    send,
)
from aiip.connectors.canonical import Case, CaseSummary, Customer

API = "/services/data/v62.0"
_CODES = {
    "INVALID_SESSION_ID": AUTH_EXPIRED,
    "INSUFFICIENT_ACCESS": AUTHZ,
    "INSUFFICIENT_ACCESS_OR_READONLY": AUTHZ,
    "INSUFFICIENT_ACCESS_ON_CROSS_REFERENCE_ENTITY": AUTHZ,
    "NOT_FOUND": NOT_FOUND,
    "ENTITY_IS_DELETED": NOT_FOUND,
    "REQUIRED_FIELD_MISSING": VALIDATION,
    "INVALID_CROSS_REFERENCE_KEY": VALIDATION,
    "MALFORMED_QUERY": VALIDATION,
    "DUPLICATE_VALUE": CONFLICT,
    "REQUEST_LIMIT_EXCEEDED": RATE_LIMITED,
    "FIELD_CUSTOM_VALIDATION_EXCEPTION": BUSINESS_REJECT,
}
_SAFE_ID = re.compile(r"^[A-Za-z0-9]{10,18}$")


class SalesforcePack(ConnectorPack):
    name = "salesforce"
    vendor = "salesforce"
    auth_strategy = "OAuth 2.0 JWT bearer (connected app key in Key Vault)"
    user_scoped_strategy = "JWT sub = Entra UPN (federation id) so sharing rules apply"
    idempotency_strategy = "PATCH upsert on External_Id__c"
    rate_limit_headers = ("Sforce-Limit-Info",)
    stored_fields = {
        "Account": ("Id", "AccountNumber", "Name", "Tier__c", "Region__c", "Credit_Hold__c"),
        "Case": ("Id", "CaseNumber", "Subject", "Status"),
    }

    def __init__(self) -> None:
        super().__init__()
        self.auth = SalesforceJwtBearer(self.http)
        self.operations = {
            "get_account": self.get_account,
            "list_cases": self.list_cases,
            "upsert_case": self.upsert_case,
        }

    def hint(self, headers: httpx.Headers) -> RateLimitHint | None:
        m = re.search(r"api-usage=(\d+)/(\d+)", headers.get("sforce-limit-info", ""))
        if not m:
            return None
        used, limit = int(m.group(1)), int(m.group(2))
        return RateLimitHint(remaining=limit - used, limit=limit, source="Sforce-Limit-Info")

    def map_error(self, resp: httpx.Response) -> ConnectorError:
        try:
            first = resp.json()[0]
            code, msg = first.get("errorCode", ""), first.get("message", "")
        except Exception:
            code, msg = "", ""
        cls = _CODES.get(code) or (TRANSIENT if resp.status_code >= 500 else VALIDATION)
        return ConnectorError(
            cls,
            msg or f"salesforce HTTP {resp.status_code}",
            vendor_code=code,
            retry_after=retry_after(resp.headers),
        )

    @staticmethod
    def to_customer(rec: dict) -> dict:
        return Customer(
            source_system="salesforce",
            customer_id=rec["Id"],
            account_number=rec["AccountNumber"],
            name=rec["Name"],
            tier=rec.get("Tier__c") or "",
            region=rec.get("Region__c") or "",
            credit_hold=bool(rec.get("Credit_Hold__c")),
        ).model_dump()

    async def _account_id(self, ref: str, ctx: ConnectorContext) -> str:
        """Accept either the Salesforce Id or the business AccountNumber (ACC-nnnn)."""
        if _SAFE_ID.match(ref) and not ref.startswith("ACC"):
            return ref
        if not re.match(r"^ACC-\d{4}$", ref):
            raise ConnectorError(VALIDATION, "account reference is not an Id or AccountNumber")
        r = await send(
            self,
            self.auth,
            "GET",
            f"{API}/query",
            ctx,
            params={"q": f"SELECT Id FROM Account WHERE AccountNumber = '{ref}'"},
        )
        recs = r.json().get("records", [])
        if not recs:  # invisible to this principal, or absent: same answer, no existence oracle
            raise ConnectorError(
                AUTHZ if ctx.principal.is_user else NOT_FOUND, "account not visible to this principal"
            )
        return recs[0]["Id"]

    async def get_account(self, args: dict, ctx: ConnectorContext) -> ConnectorResult:
        acct_id = await self._account_id(args["account_id"], ctx)
        r = await send(self, self.auth, "GET", f"{API}/sobjects/Account/{acct_id}", ctx)
        return ConnectorResult(self.to_customer(r.json()), hint=self.last_hint)

    async def list_cases(self, args: dict, ctx: ConnectorContext) -> ConnectorResult:
        acct_id = await self._account_id(args["account_id"], ctx)
        r = await send(
            self,
            self.auth,
            "GET",
            f"{API}/query",
            ctx,
            params={"q": f"SELECT Id, CaseNumber, Subject, Status FROM Case WHERE AccountId = '{acct_id}'"},
        )
        cases = [
            CaseSummary(
                source_system="salesforce",
                case_id=c["Id"],
                case_number=c["CaseNumber"],
                subject=c["Subject"],
                status=c["Status"],
            ).model_dump()
            for c in r.json().get("records", [])
        ]
        return ConnectorResult({"account_id": acct_id, "cases": cases}, hint=self.last_hint)

    async def upsert_case(self, args: dict, ctx: ConnectorContext) -> ConnectorResult:
        if not ctx.idempotency_key:
            raise ConnectorError(VALIDATION, "upsert_case needs an idempotency key (used as External_Id__c)")
        acct_id = await self._account_id(args["account_id"], ctx)
        body = {
            "AccountId": acct_id,
            "Subject": args["subject"],
            "Priority": args.get("priority", "Medium"),
            "Description": args.get("description", ""),
        }
        r = await send(
            self,
            self.auth,
            "PATCH",
            f"{API}/sobjects/Case/External_Id__c/{ctx.idempotency_key}",
            ctx,
            json=body,
        )
        data = r.json()
        if not data.get("success", False):
            errs = data.get("errors") or [{}]
            raise ConnectorError(
                BUSINESS_REJECT,
                errs[0].get("message", "upsert refused"),
                vendor_code=errs[0].get("statusCode", ""),
            )
        case = Case(
            source_system="salesforce",
            case_id=data["id"],
            external_id=ctx.idempotency_key,
            customer_id=acct_id,
            created=bool(data.get("created")),
        )
        return ConnectorResult(
            case.model_dump(),
            hint=self.last_hint,
            replayed=not data.get("created"),
            vendor_status=r.status_code,
        )
