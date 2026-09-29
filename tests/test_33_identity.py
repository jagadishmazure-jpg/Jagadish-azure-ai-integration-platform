"""Section 33 - Identity "as whom?": OBO for interactive journeys, client credentials for event
workers, hybrid; one app registration per agent; actor + subject in every log line; the ACL
difference between users; JWT validation failures; the real MSAL code path with a fake MSAL."""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from tests.conftest import agent_token, call, obo_token, tool, user_token

from aiip.config import DEMO_USERS, TENANTS
from aiip.identity import registrations as regs
from aiip.identity.client import IdentityClient
from aiip.identity.issuer import ISSUER
from aiip.identity.registrations import DATAVERSE, MCP_GW, TOOL_GW, agent_uri
from aiip.shared.auth import validate_token
from aiip.shared.errors import GatewayError


def test_one_app_registration_per_agent_worker_and_gateway():
    names = set(regs.REGISTRATIONS)
    assert {
        "care-planner",
        "crm-agent",
        "erp-agent",
        "data-agent",
        "ap-invoice-agent",
        "worker-order-events",
        "worker-shipment-events",
        "bpm-invoice-orchestrator",
    } <= names
    ids = [r.client_id for r in regs.REGISTRATIONS.values()]
    assert len(ids) == len(set(ids))
    uris = [r.app_id_uri for r in regs.REGISTRATIONS.values()]
    assert len(uris) == len(set(uris))
    for r in regs.REGISTRATIONS.values():
        assert r.secret_ref.startswith("kv://")  # a reference, never a value


async def test_registration_catalog_exposes_no_secrets():
    s, body = await call("identity", "GET", "/v1/registrations")
    text = str(body)
    assert s == 200 and "kv://" not in text and "secret" not in text.lower().replace("secret_ref", "")


async def test_obo_token_carries_user_as_subject_and_agent_as_actor():
    tok = await obo_token("alice", "crm-agent", TOOL_GW)
    p = await validate_token(tok, TOOL_GW)
    assert p.identity_mode == "obo" and p.actor == "crm-agent" and p.subject == "alice@contoso.example"
    assert "user_impersonation" in p.scopes and p.roles == ("CareRep",)


async def test_agent_token_is_the_workers_own_identity():
    tok = await agent_token("worker-shipment-events", TOOL_GW)
    p = await validate_token(tok, TOOL_GW)
    assert p.identity_mode == "agent" and p.actor == p.subject == "worker-shipment-events"
    assert set(p.roles) == set(regs.REGISTRATIONS["worker-shipment-events"].app_roles[TOOL_GW])


async def test_hybrid_returns_user_token_and_agent_token():
    user = await user_token("alice", agent_uri("erp-agent"))
    u, a = await IdentityClient("erp-agent").hybrid(user, TOOL_GW, MCP_GW)
    pu, pa = await validate_token(u, TOOL_GW), await validate_token(a, MCP_GW)
    assert (pu.identity_mode, pu.subject) == ("obo", "alice@contoso.example")
    assert (pa.identity_mode, pa.subject) == ("agent", "erp-agent")


async def test_obo_is_limited_to_pre_authorized_targets():
    user = await user_token("alice", agent_uri("crm-agent"))
    with pytest.raises(GatewayError) as e:
        await IdentityClient("crm-agent").obo(user, MCP_GW)
    assert e.value.code == "authz_deny"


async def test_obo_rejects_a_token_issued_for_another_app():
    user = await user_token("alice", agent_uri("erp-agent"))
    with pytest.raises(GatewayError):
        await IdentityClient("crm-agent").obo(user, TOOL_GW)


async def test_clients_must_authenticate_to_the_identity_gateway():
    s, _body = await call(
        "identity",
        "POST",
        "/v1/exchange/agent",
        headers={"x-client-id": regs.REGISTRATIONS["crm-agent"].client_id, "x-client-assertion": "guess"},
        json={"target": TOOL_GW},
    )
    assert s == 401


async def test_every_exchange_is_logged_with_actor_and_subject():
    await obo_token("alice", "crm-agent", TOOL_GW)
    await agent_token("worker-order-events", TOOL_GW)
    _s, body = await call("identity", "GET", "/v1/exchanges")
    rows = body["exchanges"] if "exchanges" in body else body["records"]
    modes = {(r["identity_mode"], r["actor"], r["subject"]) for r in rows}
    assert ("obo", "crm-agent", "alice@contoso.example") in modes
    assert ("agent", "worker-order-events", "worker-order-events") in modes


async def test_acl_difference_between_users_in_crm_and_dataverse():
    alice = await obo_token("alice", "crm-agent", TOOL_GW)
    bob = await obo_token("bob", "crm-agent", TOOL_GW)
    worker = await agent_token("worker-order-events", TOOL_GW)
    assert (await tool(alice, "crm.get_account", {"account_id": "ACC-1001"}))[0] == 200
    assert (await tool(bob, "crm.get_account", {"account_id": "ACC-1001"}))[0] == 403
    assert (await tool(bob, "crm.get_account", {"account_id": "ACC-1002"}))[0] == 200
    # the agent identity sees everything its role allows; that is why user journeys must not use it
    assert (await tool(worker, "crm.get_account", {"account_id": "ACC-1001"}))[0] == 200
    s_a, _ = await tool(alice, "service.get_entitlement", {"account_number": "ACC-1001"})
    s_b, body = await tool(bob, "service.get_entitlement", {"account_number": "ACC-1001"})
    assert (s_a, s_b) == (200, 403) and body["error"]["code"] == "authz_deny"


async def test_tool_gateway_exchanges_obo_again_for_dataverse():
    alice = await obo_token("alice", "crm-agent", TOOL_GW)
    await tool(alice, "service.get_entitlement", {"account_number": "ACC-1001"})
    _, body = await call("identity", "GET", "/v1/exchanges", params={"actor": "tool-gateway"})
    rows = body.get("exchanges", body.get("records"))
    assert any(r["target"] == DATAVERSE and r["subject"] == "alice@contoso.example" for r in rows)


# ------------------------------------------------------------------------ token validation
def _claims(**over):
    u = DEMO_USERS["alice"]
    c = {
        "aud": TOOL_GW,
        "tid": u.tid,
        "oid": u.oid,
        "sub": u.oid,
        "azp": regs.REGISTRATIONS["crm-agent"].client_id,
        "scp": "user_impersonation",
        "upn": u.upn,
    }
    c.update(over)
    return c


async def test_valid_local_token_passes():
    p = await validate_token(ISSUER.mint(_claims()), TOOL_GW)
    assert p.actor == "crm-agent"


@pytest.mark.parametrize(
    "mutate,reason",
    [
        (lambda: ISSUER.mint(_claims(), lifetime_s=-120), "expired"),
        (lambda: ISSUER.mint(_claims(aud=MCP_GW)), "audience"),
        (lambda: ISSUER.mint(_claims(tid="11111111-1111-1111-1111-111111111111")), "tenant"),
        (
            lambda: jwt.encode(
                {**_claims(), "exp": int(time.time()) + 60, "iat": int(time.time())},
                "k" * 32,
                algorithm="HS256",
            ),
            "algorithm",
        ),
    ],
)
async def test_invalid_tokens_are_rejected(mutate, reason):
    with pytest.raises(GatewayError) as e:
        await validate_token(mutate(), TOOL_GW)
    assert e.value.code == "authn_failed" and reason in e.value.message


async def test_token_signed_by_an_unknown_key_is_rejected():
    rogue = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = int(time.time())
    tok = jwt.encode(
        {
            **_claims(),
            "iss": f"https://login.local.test/{TENANTS['contoso']}/v2.0",
            "iat": now,
            "exp": now + 300,
        },
        rogue,
        algorithm="RS256",
        headers={"kid": ISSUER.jwks()["keys"][0]["kid"]},
    )
    with pytest.raises(GatewayError):
        await validate_token(tok, TOOL_GW)


# ------------------------------------------------------------------------ real MSAL code path
class FakeCCA:
    instances: list = []

    def __init__(self, client_id, authority, client_credential, token_cache):
        self.client_id, self.authority, self.cred = client_id, authority, client_credential
        self.calls = []
        FakeCCA.instances.append(self)

    def acquire_token_on_behalf_of(self, user_assertion, scopes):
        self.calls.append(("obo", scopes))
        return {"access_token": "obo-token"} if "tool-gateway" in scopes[0] else {"error": "invalid_grant"}

    def acquire_token_for_client(self, scopes):
        self.calls.append(("client", scopes))
        return {"access_token": "app-token"}


async def test_msal_broker_uses_obo_and_client_credentials_per_agent_registration(monkeypatch):
    import msal

    from aiip.identity.broker import MsalBroker

    FakeCCA.instances.clear()
    monkeypatch.setattr(msal, "ConfidentialClientApplication", FakeCCA)
    broker = MsalBroker()
    crm = regs.REGISTRATIONS["crm-agent"]
    user = await user_token("alice", agent_uri("crm-agent"))
    assert await broker.obo(crm, user, TOOL_GW) == "obo-token"
    assert (
        await broker.agent(regs.REGISTRATIONS["worker-order-events"], TOOL_GW, TENANTS["contoso"])
        == "app-token"
    )
    obo_app, app_app = FakeCCA.instances
    assert obo_app.client_id == crm.client_id and obo_app.calls == [("obo", [f"{TOOL_GW}/.default"])]
    assert obo_app.authority.endswith(TENANTS["contoso"])
    assert callable(
        obo_app.cred["client_assertion"]
    )  # federated credential from the gateway's managed identity
    assert app_app.client_id == regs.REGISTRATIONS["worker-order-events"].client_id
    with pytest.raises(GatewayError) as e:
        await broker.obo(crm, user, MCP_GW)
    assert e.value.code == "authz_deny"
