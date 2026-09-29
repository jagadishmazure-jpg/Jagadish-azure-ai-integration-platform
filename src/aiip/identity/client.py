"""Client used by agents, workers and gateways to obtain tokens from the Identity Gateway.
Tokens are cached in memory until shortly before expiry; nothing is persisted."""

from __future__ import annotations

import hashlib
import time

import jwt

from aiip.config import is_azure
from aiip.identity.registrations import REGISTRATIONS
from aiip.shared import errors as E
from aiip.shared import http
from aiip.shared.secrets import RESOLVER


class IdentityClient:
    def __init__(self, app_name: str) -> None:
        self.reg = REGISTRATIONS[app_name]
        self._cache: dict[str, tuple[float, str]] = {}

    async def _auth_headers(self) -> dict[str, str]:
        if is_azure():  # pragma: no cover
            from azure.identity.aio import ManagedIdentityCredential

            async with ManagedIdentityCredential() as cred:
                tok = await cred.get_token("api://aiip-identity-gateway/.default")
            return {"authorization": f"Bearer {tok.token}"}
        return {
            "x-client-id": self.reg.client_id,
            "x-client-assertion": RESOLVER.resolve(self.reg.secret_ref),
        }

    def _cached(self, key: str) -> str | None:
        hit = self._cache.get(key)
        return hit[1] if hit and hit[0] > time.time() + 60 else None

    def _store(self, key: str, token: str) -> str:
        exp = jwt.decode(token, options={"verify_signature": False}).get("exp", time.time() + 300)
        self._cache[key] = (exp, token)
        return token

    async def _post(self, path: str, body: dict) -> dict:
        async with http.client("identity") as c:
            r = await c.post(path, json=body, headers=await self._auth_headers())
        if r.status_code != 200:
            err = r.json().get("error", {})
            raise E.GatewayError(err.get("code", E.AUTHZ_DENY), err.get("message", "token exchange failed"))
        return r.json()

    async def obo(self, user_assertion: str, target: str) -> str:
        key = "obo:" + hashlib.sha256(user_assertion.encode()).hexdigest()[:16] + target
        if tok := self._cached(key):
            return tok
        data = await self._post("/v1/exchange/obo", {"user_assertion": user_assertion, "target": target})
        return self._store(key, data["access_token"])

    async def agent_token(self, target: str, tenant: str = "contoso") -> str:
        key = f"agent:{tenant}:{target}"
        if tok := self._cached(key):
            return tok
        data = await self._post("/v1/exchange/agent", {"target": target, "tenant": tenant})
        return self._store(key, data["access_token"])

    async def hybrid(self, user_assertion: str, user_target: str, agent_target: str) -> tuple[str, str]:
        data = await self._post(
            "/v1/exchange/hybrid",
            {"user_assertion": user_assertion, "user_target": user_target, "agent_target": agent_target},
        )
        return data["user_token"], data["agent_token"]


async def login(username: str, audience: str) -> str:
    """Local demo sign-in (stand-in for MSAL auth code + PKCE in a SPA)."""
    async with http.client("identity") as c:
        r = await c.post("/local/login", json={"username": username, "audience": audience})
    r.raise_for_status()
    return r.json()["access_token"]
