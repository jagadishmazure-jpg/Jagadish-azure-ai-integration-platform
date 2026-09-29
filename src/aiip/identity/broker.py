"""Token exchange. Two interchangeable brokers behind one interface:

* LocalBroker  - validates the incoming user assertion and mints downstream tokens with the local
                 issuer. Same rules as Entra: OBO only to pre-authorized targets, app-only tokens
                 only carry app roles that were explicitly granted.
* MsalBroker   - the real thing: MSAL `acquire_token_on_behalf_of` / `acquire_token_for_client`
                 for the calling agent's own app registration, authenticated with a federated
                 credential (the gateway's managed identity token), so no client secret exists."""

from __future__ import annotations

import os
from typing import Protocol

from aiip.config import USERS_BY_OID, is_azure
from aiip.identity.issuer import ISSUER
from aiip.identity.registrations import AppRegistration
from aiip.shared import errors as E
from aiip.shared.auth import validate_token


class TokenBroker(Protocol):
    async def obo(self, caller: AppRegistration, user_assertion: str, target: str) -> str: ...

    async def agent(self, caller: AppRegistration, target: str, tenant_id: str) -> str: ...


class LocalBroker:
    async def obo(self, caller: AppRegistration, user_assertion: str, target: str) -> str:
        user = await validate_token(user_assertion, caller.app_id_uri)
        if not user.is_user:
            raise E.GatewayError(E.AUTHZ_DENY, "on-behalf-of needs a delegated (user) assertion")
        if target not in caller.obo_targets:
            raise E.GatewayError(E.AUTHZ_DENY, f"{caller.name} is not pre-authorized for {target}")
        c = user.claims
        demo = USERS_BY_OID.get(user.subject_id)
        return ISSUER.mint(
            {
                "aud": target,
                "tid": user.tenant_id,
                "oid": user.subject_id,
                "sub": user.subject_id,
                "azp": caller.client_id,
                "scp": "user_impersonation",
                "upn": c.get("upn", ""),
                "name": c.get("name", ""),
                "roles": list(demo.roles) if demo else list(c.get("roles", [])),
                "region": c.get("region", demo.region if demo else ""),
            }
        )

    async def agent(self, caller: AppRegistration, target: str, tenant_id: str) -> str:
        roles = caller.app_roles.get(target)
        if not roles:
            raise E.GatewayError(E.AUTHZ_DENY, f"{caller.name} has no app role on {target}")
        return ISSUER.mint(
            {
                "aud": target,
                "tid": tenant_id,
                "oid": caller.service_principal_oid,
                "sub": caller.service_principal_oid,
                "azp": caller.client_id,
                "roles": list(roles),
                "idtyp": "app",
            }
        )


class MsalBroker:  # pragma: no cover - exercised only against real Entra ID
    """Production path. Each agent keeps its own app registration; the gateway authenticates *as that
    app* using a federated identity credential whose issuer is the gateway's managed identity."""

    def __init__(self) -> None:
        import msal

        self._msal = msal
        self._apps: dict[tuple[str, str], object] = {}
        self._mi_client_id = os.environ.get("AIIP_IDENTITY_GATEWAY_MI_CLIENT_ID", "")

    def _assertion(self) -> str:
        from azure.identity import ManagedIdentityCredential

        cred = ManagedIdentityCredential(client_id=self._mi_client_id or None)
        return cred.get_token("api://AzureADTokenExchange/.default").token

    def _app(self, caller: AppRegistration, tenant_id: str):
        key = (caller.client_id, tenant_id)
        if key not in self._apps:
            self._apps[key] = self._msal.ConfidentialClientApplication(
                caller.client_id,
                authority=f"https://login.microsoftonline.com/{tenant_id}",
                client_credential={"client_assertion": self._assertion},
                token_cache=self._msal.TokenCache(),
            )
        return self._apps[key]

    @staticmethod
    def _unwrap(result: dict) -> str:
        if "access_token" in result:
            return result["access_token"]
        err = result.get("error", "")
        code = (
            E.AUTHZ_DENY
            if err in {"invalid_grant", "consent_required", "interaction_required"}
            else E.UNAVAILABLE
        )
        raise E.GatewayError(code, f"token exchange failed: {err}")

    async def obo(self, caller: AppRegistration, user_assertion: str, target: str) -> str:
        if target not in caller.obo_targets:
            raise E.GatewayError(E.AUTHZ_DENY, f"{caller.name} is not pre-authorized for {target}")
        user = await validate_token(user_assertion, caller.app_id_uri)
        app = self._app(caller, user.tenant_id)
        return self._unwrap(app.acquire_token_on_behalf_of(user_assertion, scopes=[f"{target}/.default"]))

    async def agent(self, caller: AppRegistration, target: str, tenant_id: str) -> str:
        app = self._app(caller, tenant_id)
        return self._unwrap(app.acquire_token_for_client(scopes=[f"{target}/.default"]))


def get_broker() -> TokenBroker:
    return MsalBroker() if is_azure() else LocalBroker()
