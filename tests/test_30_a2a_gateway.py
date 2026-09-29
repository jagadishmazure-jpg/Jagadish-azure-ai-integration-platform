"""Section 30 - A2A Gateway + directory: agent cards with integration contract, caller allow-list,
hop cap, traceparent + tenant propagation, versioning; MAF domain agents behind a fan-out planner."""

from __future__ import annotations

import uuid

import httpx
import pytest
from tests.conftest import agent_token, call, user_token

from aiip.a2a import client as a2a
from aiip.a2a.cards import CONTRACT_EXT
from aiip.a2a.directory import MAX_HOPS
from aiip.a2a.specs import AGENTS
from aiip.config import TENANTS
from aiip.identity.client import IdentityClient
from aiip.identity.registrations import agent_uri
from aiip.shared.errors import GatewayError

CONTOSO = TENANTS["contoso"]
TRACE = "00-" + "a" * 32 + "-" + "b" * 16 + "-01"


def _contract(card: dict) -> dict:
    ext = next(e for e in card["capabilities"]["extensions"] if e["uri"] == CONTRACT_EXT)
    return ext["params"]


async def test_directory_lists_every_agent_with_its_contract():
    s, body = await call("a2a-gateway", "GET", "/v1/agents")
    assert s == 200 and body["max_hops"] == MAX_HOPS
    ids = {(a["id"], a["version"]) for a in body["agents"]}
    assert {
        ("care-planner", "1.0.0"),
        ("crm-agent", "1.3.0"),
        ("crm-agent", "2.0.0"),
        ("erp-agent", "1.1.0"),
        ("data-agent", "1.0.0"),
        ("ap-invoice-agent", "1.0.0"),
    } <= ids
    for a in body["agents"]:
        assert {"owner", "side_effect_class", "sla", "eval_score", "allowed_callers", "skills"} <= set(a)


@pytest.mark.parametrize(
    "app_name,agent_id",
    [
        ("crm_app", "crm-agent"),
        ("erp_app", "erp-agent"),
        ("data_app", "data-agent"),
        ("ap_invoice_app", "ap-invoice-agent"),
        ("care_planner_app", "care-planner"),
    ],
)
async def test_each_agent_serves_a_well_known_card(app_name, agent_id):
    from aiip.agents import apps

    app = getattr(apps, app_name)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as c:
        r = await c.get("/.well-known/agent-card.json")
    assert r.status_code == 200
    card = r.json()
    c = _contract(card)
    assert c["agent_id"] == agent_id and c["owner"] and c["sla"]["p95_ms"] > 0
    for skill in c["skills"].values():
        assert skill["side_effect"] in {"read", "simulate", "commit"}
        assert skill["input_schema"]["type"] == "object"
    assert card["supportedInterfaces"][0]["protocolVersion"] == "1.0"


async def test_versioning_resolves_major_and_exact_and_routes_to_v2_path():
    s, v1 = await call("a2a-gateway", "GET", "/v1/agents/crm-agent/card", params={"version": "1"})
    s, v2 = await call("a2a-gateway", "GET", "/v1/agents/crm-agent/card")
    assert v1["version"] == "1.3.0" and v2["version"] == "2.0.0"
    assert v2["supportedInterfaces"][0]["url"].endswith("/v2/a2a")
    s, _ = await call("a2a-gateway", "GET", "/v1/agents/crm-agent/card", params={"version": "9"})
    assert s == 404


async def _planner_token(user: str = "alice") -> str:
    tok = await user_token(user, agent_uri("care-planner"))
    return await IdentityClient("care-planner").obo(tok, agent_uri("crm-agent"))


async def test_v2_contract_accepts_new_shape_and_v1_rejects_it():
    tok = await _planner_token()
    out = await a2a.send(
        "crm-agent",
        "account_summary",
        {"customer": {"account_number": "ACC-1001"}},
        token=tok,
        tenant_id=CONTOSO,
        version="2",
    )
    assert out["resolved_version"] == "2.0.0" and out["output"]["account"]["account_number"] == "ACC-1001"
    with pytest.raises(GatewayError) as e:
        await a2a.send(
            "crm-agent",
            "account_summary",
            {"customer": {"account_number": "ACC-1001"}},
            token=tok,
            tenant_id=CONTOSO,
            version="1",
        )
    assert e.value.code == "validation_error"


async def test_caller_allow_list_is_enforced():
    # a user token straight to the CRM agent (skipping the planner) is refused: caller is the BFF
    direct = await user_token("alice", agent_uri("crm-agent"))
    with pytest.raises(GatewayError) as e:
        await a2a.send(
            "crm-agent",
            "account_summary",
            {"account_number": "ACC-1001"},
            token=direct,
            tenant_id=CONTOSO,
            version="1",
        )
    assert e.value.code == "authz_deny" and "allow-list" in e.value.message


async def test_hop_cap_stops_runaway_delegation():
    tok = await _planner_token()
    with pytest.raises(GatewayError) as e:
        await a2a.send(
            "crm-agent",
            "account_summary",
            {"account_number": "ACC-1001"},
            token=tok,
            tenant_id=CONTOSO,
            version="1",
            hops=MAX_HOPS,
        )
    assert e.value.code == "authz_deny" and "hop cap" in e.value.message


async def test_tenant_header_must_match_token():
    tok = await _planner_token()
    with pytest.raises(GatewayError) as e:
        await a2a.send(
            "crm-agent",
            "account_summary",
            {"account_number": "ACC-1001"},
            token=tok,
            tenant_id=TENANTS["fabrikam"],
            version="1",
        )
    assert e.value.code == "authz_deny"


async def test_a2a_version_header_is_required():
    tok = await _planner_token()
    body = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "SendMessage",
        "params": {
            "message": {
                "messageId": uuid.uuid4().hex,
                "role": "ROLE_USER",
                "parts": [{"data": {"skill": "account_summary", "input": {"account_number": "ACC-1001"}}}],
            }
        },
    }
    s, r = await call(
        "a2a-gateway", "POST", "/v1/agents/crm-agent/a2a", tok, json=body, headers={"x-agent-version": "1"}
    )
    assert s == 400 and "A2A-Version" in r["error"]["message"]


async def test_traceparent_is_propagated_to_the_callee_and_audited():
    from aiip.a2a import gateway as gw

    tok = await _planner_token()
    await a2a.send(
        "crm-agent",
        "account_summary",
        {"account_number": "ACC-1001"},
        token=tok,
        tenant_id=CONTOSO,
        version="1",
        traceparent=TRACE,
    )
    rec = gw.AUDIT.records[-1]
    assert (
        rec["trace_id"] == "a" * 32
        and rec["subject"] == "alice@contoso.example"
        and rec["actor"] == "care-planner"
    )
    assert rec["hops"] == 1


async def test_planner_fans_out_and_every_hop_acts_as_the_user():
    from aiip.tools import gateway as tool_gw

    tok = await user_token("alice", agent_uri("care-planner"))
    out = await a2a.send(
        "care-planner",
        "resolve_customer_request",
        {"account_number": "ACC-1001", "order_id": "4500001", "question": "status?"},
        token=tok,
        tenant_id=CONTOSO,
        timeout_s=60,
    )
    res = out["output"]
    assert res["fan_out"] == ["crm", "data", "erp"] and res["result_class"] == "ok"
    assert "7 day(s) late" in res["answer"]
    obo_rows = [r for r in tool_gw.AUDIT.records if r["identity_mode"] == "obo"]
    assert obo_rows and all(r["subject"] == "alice@contoso.example" for r in obo_rows)
    assert {r["actor"] for r in obo_rows} >= {"crm-agent", "erp-agent"}


async def test_domain_agents_run_on_microsoft_agent_framework():
    from agent_framework import BaseChatClient

    from aiip.agents.maf import PlanFollowingChatClient

    assert issubclass(PlanFollowingChatClient, BaseChatClient)


def test_every_agent_has_owner_version_and_sla():
    for a in AGENTS:
        assert a.owner and a.version.count(".") == 2
        if a.kind == "a2a-agent":
            assert a.sla_p95_ms > 0 and a.allowed_callers


async def test_worker_can_only_reach_agents_it_holds_a_role_for():
    await agent_token("worker-shipment-events", agent_uri("ap-invoice-agent"))
    with pytest.raises(GatewayError):
        await agent_token("worker-order-events", agent_uri("ap-invoice-agent"))
