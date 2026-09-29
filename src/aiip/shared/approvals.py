"""Human-in-the-loop approvals for commits. An approval is bound to one tool and one exact set of
arguments (by digest), is decided by a *user* principal with the right role, and is single-use."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime

from aiip.shared import errors as E
from aiip.shared.audit import digest
from aiip.shared.auth import Principal


@dataclass
class Approval:
    id: str
    tool: str
    args_digest: str
    business_key: str
    requested_by: str
    tenant_id: str
    approver_role: str
    status: str = "pending"  # pending | approved | rejected | used
    decided_by: str = ""
    decided_at: str = ""
    history: list[str] = field(default_factory=list)


class ApprovalStore:
    def __init__(self) -> None:
        self.items: dict[str, Approval] = {}

    def request(
        self, tool: str, args: dict, business_key: str, requester: Principal, approver_role: str
    ) -> Approval:
        a = Approval(
            id="apr-" + secrets.token_hex(6),
            tool=tool,
            args_digest=digest(args),
            business_key=business_key,
            requested_by=requester.actor,
            tenant_id=requester.tenant_id,
            approver_role=approver_role,
        )
        self.items[a.id] = a
        return a

    def decide(self, approval_id: str, approved: bool, approver: Principal) -> Approval:
        a = self.items.get(approval_id)
        if a is None:
            raise E.GatewayError(E.NOT_FOUND, "unknown approval")
        if a.status != "pending":
            raise E.GatewayError(E.CONFLICT, f"approval already {a.status}")
        if not approver.is_user or a.approver_role not in approver.claims.get("roles", []):
            raise E.GatewayError(E.AUTHZ_DENY, f"approver needs a user token with role {a.approver_role}")
        if approver.tenant_id != a.tenant_id:
            raise E.GatewayError(E.AUTHZ_DENY, "cross-tenant approval")
        a.status = "approved" if approved else "rejected"
        a.decided_by, a.decided_at = approver.subject, datetime.now(UTC).isoformat(timespec="seconds")
        return a

    def check(self, approval_id: str | None, tool: str, args: dict, tenant_id: str) -> Approval:
        """Validate without consuming; `mark_used` after the commit succeeds so a transient failure
        can be retried under the same approval."""
        a = self.items.get(approval_id or "")
        if a is None:
            raise E.GatewayError(
                E.APPROVAL_REQUIRED, f"{tool} needs a recorded human approval (x-approval-id)"
            )
        if a.tool != tool or a.args_digest != digest(args) or a.tenant_id != tenant_id:
            raise E.GatewayError(E.AUTHZ_DENY, "approval does not match this tool call")
        if a.status != "approved":
            raise E.GatewayError(E.APPROVAL_REQUIRED, f"approval is {a.status}")
        return a

    @staticmethod
    def mark_used(a: Approval) -> None:
        a.status = "used"

    def reset(self) -> None:
        self.items.clear()
