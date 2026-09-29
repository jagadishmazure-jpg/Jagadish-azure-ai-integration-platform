"""Runtime settings and the demo directory of tenants and people.

`AIIP_MODE=local` (default) runs everything against the local token issuer, the local secret
store and the in-memory bus. `AIIP_MODE=azure` switches the same code to Entra ID (MSAL),
Key Vault, Service Bus and Event Grid. Nothing in this module holds a credential."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

_NS = uuid.UUID("6f1c2a4e-0d7b-4c55-9a51-3b1f0e2d9c10")


def stable_id(name: str) -> str:
    """Deterministic GUID-shaped id for demo objects (tenants, users, app registrations)."""
    return str(uuid.uuid5(_NS, name))


def mode() -> str:
    return os.environ.get("AIIP_MODE", "local").lower()


def is_azure() -> bool:
    return mode() == "azure"


TENANTS = {"contoso": stable_id("tenant:contoso"), "fabrikam": stable_id("tenant:fabrikam")}
TENANT_NAMES = {v: k for k, v in TENANTS.items()}


@dataclass(frozen=True)
class DemoUser:
    username: str
    display_name: str
    tenant: str
    region: str
    roles: tuple[str, ...]

    @property
    def oid(self) -> str:
        return stable_id(f"user:{self.username}")

    @property
    def tid(self) -> str:
        return TENANTS[self.tenant]

    @property
    def upn(self) -> str:
        return f"{self.username}@{self.tenant}.example"


# Fictional people. `region` drives record-level sharing in the CRM stand-in.
DEMO_USERS = {
    u.username: u
    for u in (
        DemoUser("alice", "Alice (care rep, East)", "contoso", "east", ("CareRep",)),
        DemoUser("bob", "Bob (care rep, West)", "contoso", "west", ("CareRep",)),
        DemoUser("carol", "Carol (AP approver)", "contoso", "east", ("APApprover",)),
        DemoUser("dave", "Dave (care rep, Fabrikam)", "fabrikam", "east", ("CareRep",)),
        DemoUser("olivia", "Olivia (integration ops lead)", "contoso", "east", ("OpsApprover",)),
    )
}
USERS_BY_OID = {u.oid: u for u in DEMO_USERS.values()}


def issuer_for(tid: str) -> str:
    if is_azure():
        return f"https://login.microsoftonline.com/{tid}/v2.0"
    return f"https://login.local.test/{tid}/v2.0"


def jwks_url() -> str | None:
    """Azure: Entra JWKS. Local: None -> fetched from the identity gateway service."""
    if is_azure():
        tid = os.environ.get("AZURE_TENANT_ID", "common")
        return f"https://login.microsoftonline.com/{tid}/discovery/v2.0/keys"
    return None
