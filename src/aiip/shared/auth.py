"""Entra ID JWT validation shared by all five gateways, the agents and the MCP servers.

* RS256 only, signature from JWKS (Entra's in Azure, the identity gateway's in local mode).
* `aud` must be this service's app ID URI; `iss` must match the token's own tenant, which must be
  on the allow-list; `exp`/`nbf` enforced.
* The resulting Principal carries *both* the actor (which app/agent is calling, from `azp`) and the
  subject (the user from `oid`/`upn` for delegated tokens, or the agent itself for app-only tokens)."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from fastapi import Request

from aiip.config import TENANT_NAMES, TENANTS, is_azure, issuer_for, jwks_url
from aiip.identity.registrations import by_client_id
from aiip.shared import errors as E
from aiip.shared import http


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    actor_app_id: str
    actor: str
    subject_id: str
    subject: str
    identity_mode: str  # "obo" (delegated, user present) | "agent" (app-only workload identity)
    audience: str
    scopes: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    raw: str = field(default="", repr=False)
    claims: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def tenant(self) -> str:
        return TENANT_NAMES.get(self.tenant_id, self.tenant_id)

    @property
    def is_user(self) -> bool:
        return self.identity_mode == "obo"

    def audit_fields(self) -> dict[str, str]:
        return {
            "tenant": self.tenant,
            "actor": self.actor,
            "actor_app_id": self.actor_app_id,
            "subject": self.subject,
            "subject_id": self.subject_id,
            "identity_mode": self.identity_mode,
        }


def allowed_tenants() -> set[str]:
    extra = {t for t in os.environ.get("AIIP_ALLOWED_TENANTS", "").split(",") if t}
    return set(TENANTS.values()) | extra


class JwksCache:
    def __init__(self, ttl_s: float = 600) -> None:
        self.ttl_s, self._keys, self._at = ttl_s, {}, 0.0

    async def key_for(self, kid: str):
        if kid not in self._keys or time.monotonic() - self._at > self.ttl_s:
            await self.refresh()
        if kid not in self._keys:
            raise E.GatewayError(E.AUTHN_FAILED, "unknown signing key")
        return self._keys[kid]

    async def refresh(self) -> None:
        url = jwks_url()
        if url:  # pragma: no cover - Azure
            async with httpx.AsyncClient(timeout=5) as c:
                data = (await c.get(url)).json()
        else:
            async with http.client("identity") as c:
                data = (await c.get("/discovery/v2.0/keys")).json()
        self._keys = {k["kid"]: jwt.PyJWK(k).key for k in data.get("keys", [])}
        self._at = time.monotonic()


JWKS = JwksCache()


async def validate_token(token: str, audience: str | list[str]) -> Principal:
    try:
        header = jwt.get_unverified_header(token)
        unverified = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError as exc:
        raise E.GatewayError(E.AUTHN_FAILED, "malformed token") from exc
    if header.get("alg") != "RS256":
        raise E.GatewayError(E.AUTHN_FAILED, "unsupported token algorithm")
    tid = unverified.get("tid", "")
    if tid not in allowed_tenants():
        raise E.GatewayError(E.AUTHN_FAILED, "tenant not allowed")
    key = await JWKS.key_for(header.get("kid", ""))
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=audience,
            issuer=issuer_for(tid),
            options={"require": ["exp", "iat", "aud", "iss", "tid"]},
            leeway=30,
        )
    except jwt.ExpiredSignatureError as exc:
        raise E.GatewayError(E.AUTHN_FAILED, "token expired") from exc
    except jwt.InvalidAudienceError as exc:
        raise E.GatewayError(E.AUTHN_FAILED, "token audience mismatch") from exc
    except jwt.PyJWTError as exc:
        raise E.GatewayError(E.AUTHN_FAILED, "token rejected") from exc
    return principal_from_claims(claims, token)


def principal_from_claims(claims: dict[str, Any], raw: str = "") -> Principal:
    app_id = claims.get("azp") or claims.get("appid") or ""
    reg = by_client_id(app_id)
    actor = reg.name if reg else f"app:{app_id[:8]}"
    delegated = "scp" in claims
    if delegated:
        subject_id = claims.get("oid", "")
        subject = claims.get("upn") or claims.get("preferred_username") or subject_id
    else:
        subject_id = claims.get("oid", "")
        subject = actor
    return Principal(
        tenant_id=claims["tid"],
        actor_app_id=app_id,
        actor=actor,
        subject_id=subject_id,
        subject=subject,
        identity_mode="obo" if delegated else "agent",
        audience=claims.get("aud", ""),
        scopes=tuple((claims.get("scp") or "").split()),
        roles=tuple(claims.get("roles") or ()),
        raw=raw,
        claims=claims,
    )


def bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise E.GatewayError(E.AUTHN_FAILED, "bearer token required")
    return auth.split(" ", 1)[1].strip()


def principal_dependency(audience: str | list[str]):
    async def dep(request: Request) -> Principal:
        return await validate_token(bearer(request), audience)

    return dep


def local_mode_only() -> None:
    if is_azure():
        raise E.GatewayError(E.NOT_FOUND, "not available in azure mode")
