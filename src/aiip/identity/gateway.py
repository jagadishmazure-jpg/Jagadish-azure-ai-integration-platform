"""Identity Gateway (FastAPI). The only place in the platform where tokens are exchanged.

POST /v1/exchange/obo     user-scoped: the agent presents the user's token, gets one for a downstream API
POST /v1/exchange/agent   agent-scoped: client credentials for the calling agent's own registration
POST /v1/exchange/hybrid  both at once: user token for user data, agent token for shared reference data
GET  /v1/registrations    the app-registration catalog (no secrets)
GET  /v1/exchanges        exchange audit (actor + subject + mode)
Local mode only: GET /discovery/v2.0/keys (JWKS) and POST /local/login (simulated interactive sign-in)."""

from __future__ import annotations

import hmac
import os

from fastapi import FastAPI, Header, Request
from pydantic import BaseModel

from aiip.config import DEMO_USERS, TENANTS, is_azure
from aiip.identity import registrations as regs
from aiip.identity.broker import get_broker
from aiip.identity.issuer import ISSUER
from aiip.shared import errors as E
from aiip.shared.audit import AuditLog
from aiip.shared.auth import bearer, local_mode_only, validate_token
from aiip.shared.secrets import RESOLVER
from aiip.shared.telemetry import integration_span

app = FastAPI(title="Identity Gateway", version="1.0.0")
E.install_error_handlers(app)
AUDIT = AuditLog("identity-gateway")
BROKER = get_broker()
SELF_URI = "api://aiip-identity-gateway"


class OboRequest(BaseModel):
    user_assertion: str
    target: str


class AgentRequest(BaseModel):
    target: str
    tenant: str = "contoso"


class HybridRequest(BaseModel):
    user_assertion: str
    user_target: str
    agent_target: str


class LoginRequest(BaseModel):
    username: str
    audience: str


async def authenticate_caller(request: Request, x_client_id: str | None, x_client_assertion: str | None):
    """Which app is asking? Local: client id + stand-in credential resolved from the vault ref.
    Azure: the caller's managed-identity token (aud = this gateway), mapped to its registration."""
    if is_azure():  # pragma: no cover
        p = await validate_token(bearer(request), SELF_URI)
        mapping = dict(
            x.split("=", 1) for x in os.environ.get("AIIP_MI_PRINCIPALS", "").split(",") if "=" in x
        )
        reg = regs.REGISTRATIONS.get(mapping.get(p.subject_id, ""))
    else:
        reg = regs.by_client_id(x_client_id)
        if reg is None or not x_client_assertion:
            raise E.GatewayError(E.AUTHN_FAILED, "unknown client")
        expected = RESOLVER.resolve(reg.secret_ref)
        if not hmac.compare_digest(expected, x_client_assertion):
            raise E.GatewayError(E.AUTHN_FAILED, "client authentication failed")
    if reg is None:
        raise E.GatewayError(E.AUTHN_FAILED, "unknown client")
    return reg


def _record(mode: str, caller, target: str, subject: str, tenant: str, result: str) -> None:
    AUDIT.write(
        gateway="identity",
        operation=f"exchange.{mode}",
        identity_mode="obo" if mode == "obo" else "agent",
        actor=caller.name,
        subject=subject,
        tenant=tenant,
        target=target,
        result_class=result,
    )


async def _obo(caller, assertion: str, target: str) -> tuple[str, str]:
    subject, tenant = "unknown", ""
    try:
        claims = __import__("jwt").decode(assertion, options={"verify_signature": False})
        subject, tenant = claims.get("upn") or claims.get("oid", "unknown"), claims.get("tid", "")
    except Exception:
        pass
    with integration_span(
        "identity.obo",
        service="identity-gateway",
        system="entra",
        operation="obo",
        identity_mode="obo",
        actor=caller.name,
        subject=subject,
        tenant=tenant,
    ) as s:
        try:
            token = await BROKER.obo(caller, assertion, target)
        except E.GatewayError as exc:
            _record("obo", caller, target, subject, tenant, exc.code)
            s.set_result(exc.code)
            raise
    _record("obo", caller, target, subject, tenant, E.OK)
    return token, subject


async def _agent(caller, target: str, tenant: str) -> str:
    tid = TENANTS.get(tenant, tenant)
    with integration_span(
        "identity.agent",
        service="identity-gateway",
        system="entra",
        operation="client_credentials",
        identity_mode="agent",
        actor=caller.name,
        subject=caller.name,
        tenant=tid,
    ) as s:
        try:
            token = await BROKER.agent(caller, target, tid)
        except E.GatewayError as exc:
            _record("agent", caller, target, caller.name, tid, exc.code)
            s.set_result(exc.code)
            raise
    _record("agent", caller, target, caller.name, tid, E.OK)
    return token


@app.post("/v1/exchange/obo")
async def exchange_obo(
    body: OboRequest,
    request: Request,
    x_client_id: str | None = Header(default=None),
    x_client_assertion: str | None = Header(default=None),
):
    caller = await authenticate_caller(request, x_client_id, x_client_assertion)
    token, subject = await _obo(caller, body.user_assertion, body.target)
    return {
        "access_token": token,
        "token_type": "Bearer",
        "mode": "obo",
        "actor": caller.name,
        "subject": subject,
    }


@app.post("/v1/exchange/agent")
async def exchange_agent(
    body: AgentRequest,
    request: Request,
    x_client_id: str | None = Header(default=None),
    x_client_assertion: str | None = Header(default=None),
):
    caller = await authenticate_caller(request, x_client_id, x_client_assertion)
    token = await _agent(caller, body.target, body.tenant)
    return {"access_token": token, "token_type": "Bearer", "mode": "agent", "actor": caller.name}


@app.post("/v1/exchange/hybrid")
async def exchange_hybrid(
    body: HybridRequest,
    request: Request,
    x_client_id: str | None = Header(default=None),
    x_client_assertion: str | None = Header(default=None),
):
    caller = await authenticate_caller(request, x_client_id, x_client_assertion)
    user_token, subject = await _obo(caller, body.user_assertion, body.user_target)
    user = await validate_token(user_token, body.user_target)
    agent_token = await _agent(caller, body.agent_target, user.tenant_id)
    return {"user_token": user_token, "agent_token": agent_token, "mode": "hybrid", "subject": subject}


@app.get("/v1/registrations")
async def registrations():
    return {"registrations": regs.public_view()}


@app.get("/v1/exchanges")
async def exchanges(actor: str | None = None, subject: str | None = None):
    records = AUDIT.query(actor=actor, subject=subject)
    mix: dict[str, int] = {}
    for r in records:
        if r["result_class"] == E.OK:
            mix[r["identity_mode"]] = mix.get(r["identity_mode"], 0) + 1
    return {"exchanges": records[-200:], "identity_mix": mix, "chain_ok": AUDIT.verify_chain()}


@app.get("/discovery/v2.0/keys")
async def jwks():
    local_mode_only()
    return ISSUER.jwks()


@app.post("/local/login")
async def local_login(body: LoginRequest):
    """Simulates the SPA's MSAL auth-code + PKCE sign-in. Returns a delegated token for `audience`."""
    local_mode_only()
    user = DEMO_USERS.get(body.username)
    reg = regs.BY_URI.get(body.audience)
    if user is None or reg is None:
        raise E.GatewayError(E.NOT_FOUND, "unknown user or audience")
    token = ISSUER.mint(
        {
            "aud": body.audience,
            "tid": user.tid,
            "oid": user.oid,
            "sub": user.oid,
            "azp": regs.REGISTRATIONS["experience-bff"].client_id,
            "scp": "user_impersonation",
            "upn": user.upn,
            "name": user.display_name,
            "roles": list(user.roles),
            "region": user.region,
        }
    )
    return {"access_token": token, "token_type": "Bearer"}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "service": "identity-gateway", "mode": "azure" if is_azure() else "local"}
