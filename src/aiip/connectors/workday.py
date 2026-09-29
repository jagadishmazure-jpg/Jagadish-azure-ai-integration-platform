"""Workday connector pack (sandbox stand-in target). Read-only.

auth         OAuth refresh-token grant for an Integration System User (token in Key Vault)
minimal data nationalId, dateOfBirth, compensation, homeAddress are never mapped or stored"""

from __future__ import annotations

from aiip.connectors.auth import WorkdayRefreshToken
from aiip.connectors.base import ConnectorContext, ConnectorPack, ConnectorResult, send
from aiip.connectors.canonical import Worker

TENANT = "contoso_sandbox"


class WorkdayPack(ConnectorPack):
    name = "workday"
    vendor = "workday"
    auth_strategy = "OAuth 2.0 refresh token for an Integration System User (Key Vault)"
    user_scoped_strategy = "read-only reference data; agent-scoped by design"
    idempotency_strategy = "n/a (read-only pack)"
    rate_limit_headers = ("Retry-After",)
    stored_fields = {
        "worker": (
            "id",
            "descriptor",
            "primaryWorkEmail",
            "businessTitle",
            "costCenter.id",
            "manager.id",
            "isManager",
            "approvalLimit.amount",
        )
    }

    def __init__(self) -> None:
        super().__init__()
        self.auth = WorkdayRefreshToken(self.http, TENANT)
        self.operations = {"get_worker": self.get_worker, "find_approver": self.find_approver}

    @staticmethod
    def to_worker(w: dict) -> dict:
        return Worker(
            source_system="workday",
            worker_id=w["id"],
            name=w["descriptor"],
            email=w["primaryWorkEmail"],
            title=w["businessTitle"],
            cost_center=w["costCenter"]["id"],
            manager_id=w.get("manager", {}).get("id", ""),
            is_manager=bool(w.get("isManager")),
            approval_limit=float(w.get("approvalLimit", {}).get("amount", 0)),
        ).model_dump()

    async def get_worker(self, args: dict, ctx: ConnectorContext) -> ConnectorResult:
        r = await send(self, self.auth, "GET", f"/ccx/api/v1/{TENANT}/workers/{args['worker_id']}", ctx)
        return ConnectorResult(self.to_worker(r.json()))

    async def find_approver(self, args: dict, ctx: ConnectorContext) -> ConnectorResult:
        r = await send(
            self,
            self.auth,
            "GET",
            f"/ccx/api/v1/{TENANT}/workers",
            ctx,
            params={"costCenter": args["cost_center"], "isManager": "true"},
        )
        rows = [
            self.to_worker(w)
            for w in r.json()["data"]
            if float(w.get("approvalLimit", {}).get("amount", 0)) >= float(args.get("amount", 0))
        ]
        return ConnectorResult({"approver": rows[0] if rows else None})
