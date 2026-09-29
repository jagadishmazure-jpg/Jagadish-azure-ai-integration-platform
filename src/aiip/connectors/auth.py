"""Auth adapters: one per vendor dialect. Every credential is a Key Vault reference; tokens are
cached in memory per (vendor, principal) and refreshed on auth_expired."""

from __future__ import annotations

import base64
import time

import jwt

from aiip.connectors.base import AUTH_EXPIRED, AUTHZ, ConnectorError, VendorHttp, retry_after, status_class
from aiip.identity.client import IdentityClient
from aiip.shared.auth import Principal
from aiip.shared.secrets import RESOLVER


def token_failure(r, refused_class: str, message: str, vendor_code: str = "") -> ConnectorError:
    """A token endpoint that is down or throttling is an outage, not a credential problem: only a
    4xx refusal (other than 429) means the credential itself was rejected."""
    if r.status_code == 429 or r.status_code >= 500:
        return ConnectorError(
            status_class(r.status_code) or "transient",
            f"token endpoint unavailable (HTTP {r.status_code})",
            retry_after=retry_after(r.headers),
        )
    return ConnectorError(refused_class, message, vendor_code=vendor_code)


def _err(r) -> str:
    try:
        return str(r.json().get("error", ""))
    except ValueError:
        return ""


class _TokenCache:
    def __init__(self) -> None:
        self._c: dict[str, tuple[float, str]] = {}

    def get(self, key: str) -> str | None:
        hit = self._c.get(key)
        return hit[1] if hit and hit[0] > time.time() else None

    def put(self, key: str, token: str, ttl: float) -> str:
        self._c[key] = (time.time() + ttl - 60, token)
        return token

    def drop(self, key: str) -> None:
        self._c.pop(key, None)


def integration_user(actor: str) -> str:
    """Agent-scoped work runs as a *named* integration user per agent, never a shared admin."""
    return f"svc-{actor}@integration.example"


class SalesforceJwtBearer:
    """OAuth 2.0 JWT bearer flow. User-scoped: `sub` is the Entra user's federated username, so
    Salesforce sharing rules apply. Agent-scoped: `sub` is that agent's own integration user.
    (Production signs RS256 with the connected app's certificate held in Key Vault; the sandbox
    uses HS256 with a vault-held key.)"""

    KEY_REF = "kv://aiip-local-kv/salesforce-connected-app-key"
    CONSUMER_KEY = "aiip-connected-app"

    def __init__(self, http: VendorHttp) -> None:
        self.http, self.cache = http, _TokenCache()

    def username(self, p: Principal) -> str:
        return p.subject if p.is_user else integration_user(p.actor)

    async def headers(self, p: Principal, timeout_s: float) -> dict[str, str]:
        user = self.username(p)
        if tok := self.cache.get(user):
            return {"authorization": f"Bearer {tok}"}
        assertion = jwt.encode(
            {
                "iss": self.CONSUMER_KEY,
                "sub": user,
                "aud": "https://login.salesforce.com",
                "exp": int(time.time()) + 180,
            },
            RESOLVER.resolve(self.KEY_REF),
            algorithm="HS256",
        )
        r = await self.http.request(
            "POST",
            "/services/oauth2/token",
            timeout_s,
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
        )
        if r.status_code != 200:
            raise token_failure(r, AUTHZ, "salesforce refused the JWT bearer grant", vendor_code=_err(r))
        return {"authorization": f"Bearer {self.cache.put(user, r.json()['access_token'], 3600)}"}

    def invalidate(self, p: Principal) -> None:
        self.cache.drop(self.username(p))


class OAuthClientCredentials:
    """client_credentials against the vendor's token endpoint (SAP BTP/S4, ServiceNow)."""

    def __init__(self, http: VendorHttp, token_path: str, client_id: str, secret_ref: str) -> None:
        self.http, self.token_path, self.client_id, self.secret_ref = http, token_path, client_id, secret_ref
        self.cache = _TokenCache()

    async def headers(self, p: Principal, timeout_s: float) -> dict[str, str]:
        if tok := self.cache.get("app"):
            return {"authorization": f"Bearer {tok}"}
        r = await self.http.request(
            "POST",
            self.token_path,
            timeout_s,
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": RESOLVER.resolve(self.secret_ref),
            },
        )
        if r.status_code != 200:
            raise token_failure(
                r, AUTH_EXPIRED, f"{self.http.vendor} token endpoint refused client credentials"
            )
        body = r.json()
        return {
            "authorization": f"Bearer {self.cache.put('app', body['access_token'], float(body.get('expires_in', 1800)))}"
        }

    def invalidate(self, p: Principal) -> None:
        self.cache.drop("app")


class WorkdayRefreshToken:
    """Workday API client for an Integration System User: refresh_token grant."""

    REF = "kv://aiip-local-kv/workday-isu-refresh-token"

    def __init__(self, http: VendorHttp, tenant: str = "contoso_sandbox") -> None:
        self.http, self.tenant, self.cache = http, tenant, _TokenCache()

    async def headers(self, p: Principal, timeout_s: float) -> dict[str, str]:
        if tok := self.cache.get("isu"):
            return {"authorization": f"Bearer {tok}"}
        r = await self.http.request(
            "POST",
            f"/ccx/oauth2/{self.tenant}/token",
            timeout_s,
            data={
                "grant_type": "refresh_token",
                "refresh_token": RESOLVER.resolve(self.REF),
                "client_id": "aiip-workday-client",
            },
        )
        if r.status_code != 200:
            raise token_failure(r, AUTH_EXPIRED, "workday refused the refresh token")
        return {"authorization": f"Bearer {self.cache.put('isu', r.json()['access_token'], 3600)}"}

    def invalidate(self, p: Principal) -> None:
        self.cache.drop("isu")


class EntraObo:
    """Dataverse trusts Entra directly: the tool gateway (middle tier) exchanges the caller's token
    for one scoped to the environment URL. Row security is then enforced by Dataverse itself."""

    def __init__(self, target: str) -> None:
        self.target = target
        self.identity = IdentityClient("tool-gateway")

    async def headers(self, p: Principal, timeout_s: float) -> dict[str, str]:
        if not p.is_user:
            raise ConnectorError(AUTHZ, "dataverse connector is user-scoped only")
        return {"authorization": f"Bearer {await self.identity.obo(p.raw, self.target)}"}

    def invalidate(self, p: Principal) -> None:
        return None


class BasicApiToken:
    """Jira Cloud: service account + API token (from Key Vault) over basic auth."""

    def __init__(self, user: str, token_ref: str) -> None:
        self.user, self.token_ref = user, token_ref

    async def headers(self, p: Principal, timeout_s: float) -> dict[str, str]:
        raw = f"{self.user}:{RESOLVER.resolve(self.token_ref)}".encode()
        return {"authorization": "Basic " + base64.b64encode(raw).decode()}

    def invalidate(self, p: Principal) -> None:
        return None
