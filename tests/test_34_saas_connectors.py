"""Section 34 - SaaS connector packs (contract tests against the sandbox stand-ins): auth adapter,
canonical mapping, idempotent writes, error taxonomy, rate-limit hints, minimal fields."""

from __future__ import annotations

import httpx
import pytest
from pydantic import ValidationError
from tests.conftest import agent_token, call, obo_token, tool

from aiip.connectors import canonical as C
from aiip.connectors.base import ERROR_CLASSES, ConnectorContext, ConnectorError
from aiip.connectors.registry import packs, resolve
from aiip.identity.registrations import TOOL_GW
from aiip.shared.auth import validate_token

EXPECTED = {"salesforce", "servicenow", "workday", "dataverse", "sap_odata", "jira"}


async def ctx_for(app="bpm-invoice-orchestrator", key=None, user=None) -> ConnectorContext:
    tok = await (obo_token(user, "crm-agent", TOOL_GW) if user else agent_token(app, TOOL_GW))
    return ConnectorContext(principal=await validate_token(tok, TOOL_GW), idempotency_key=key)


def test_one_pack_per_saas_and_each_declares_its_contract():
    ps = packs()
    assert set(ps) == EXPECTED
    for name, p in ps.items():
        d = p.describe()
        assert d["standin"] is True, name
        assert d["auth"] and d["idempotency"] and d["user_scoped"] and d["stored_fields"], name
        assert d["operations"], name


async def test_every_sandbox_response_is_labeled_as_a_stand_in():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=__import__("aiip.fakesaas.app", fromlist=["app"]).app),
        base_url="http://saas",
    ) as c:
        r = await c.get("/")
    assert r.headers["x-aiip-sandbox-standin"] == "true" and "not Salesforce" in r.json()["note"]


async def test_salesforce_maps_to_canonical_customer_and_drops_sensitive_fields():
    alice = await obo_token("alice", "crm-agent", TOOL_GW)
    _s, body = await tool(alice, "crm.get_account", {"account_id": "ACC-1001"})
    acct = body["data"]
    C.Customer.model_validate(acct)
    assert acct["name"] == "Northwind Traders" and acct["region"] == "east"
    for leaked in ("Phone", "phone", "Tax_Id__c", "tax_id", "AnnualRevenue"):
        assert leaked not in acct
    assert body["rate_limit_hint"]["source"] == "Sforce-Limit-Info"


async def test_workday_never_maps_sensitive_hr_fields():
    tok = await agent_token("bpm-invoice-orchestrator", TOOL_GW)
    _s, body = await tool(tok, "hr.find_approver", {"cost_center": "CC-100", "amount": 12000})
    approver = body["data"]["approver"]
    C.Worker.model_validate(approver)
    for leaked in ("nationalId", "dateOfBirth", "compensation", "homeAddress"):
        assert leaked not in str(approver)


def test_canonical_models_forbid_unmapped_fields():
    with pytest.raises(ValidationError):
        C.Worker(
            source_system="workday",
            worker_id="W-1",
            name="x",
            email="x@y",
            title="t",
            cost_center="CC-1",
            manager_id="",
            is_manager=False,
            approval_limit=0,
            nationalId="123",
        )


@pytest.mark.parametrize(
    "connector,args,id_field",
    [
        ("salesforce.upsert_case", {"account_id": "ACC-1001", "subject": "Idempotency check"}, "case_id"),
        (
            "servicenow.create_incident",
            {"short_description": "Idempotency check", "business_key": "BK-1", "priority": 3},
            "number",
        ),
        (
            "jira.create_issue",
            {"project": "INT", "summary": "Idempotency check", "business_key": "BK-2"},
            "issue_key",
        ),
        (
            "sap_odata.park_invoice",
            {
                "po_id": "4500009002",
                "supplier_id": "V-51",
                "amount": "1200.00",
                "tax_code": "V1",
                "vendor_invoice_no": "INV-8100",
            },
            "invoice_document",
        ),
    ],
)
async def test_writes_are_idempotent_at_the_vendor(connector, args, id_field):
    pack, op = resolve(connector)
    ctx = await ctx_for(user="alice" if connector.startswith("salesforce") else None, key="contract-key-0001")
    first = await pack.invoke(op, args, ctx)
    second = await pack.invoke(op, args, ctx)
    assert first.data[id_field] == second.data[id_field]


async def test_dataverse_upsert_uses_alternate_key():
    alice = await obo_token("alice", "crm-agent", TOOL_GW)
    pack, op = resolve("dataverse.upsert_incident")
    ctx = ConnectorContext(principal=await validate_token(alice, TOOL_GW), idempotency_key="dv-key-0001")
    a = await pack.invoke(op, {"account_number": "ACC-1001", "title": "Late shipment"}, ctx)
    b = await pack.invoke(op, {"account_number": "ACC-1001", "title": "Late shipment"}, ctx)
    assert a.data["created"] is True and b.data["created"] is False and b.replayed
    assert a.data["ticketnumber"] == b.data["ticketnumber"] == "dv-key-0001"


def _resp(status: int, json_body, headers=None) -> httpx.Response:
    return httpx.Response(
        status, json=json_body, headers=headers or {}, request=httpx.Request("GET", "http://x")
    )


@pytest.mark.parametrize(
    "pack_name,resp,expected",
    [
        (
            "salesforce",
            _resp(400, [{"errorCode": "INVALID_FIELD", "message": "No such column"}]),
            "validation",
        ),
        ("salesforce", _resp(403, [{"errorCode": "INSUFFICIENT_ACCESS", "message": "no"}]), "authz_deny"),
        (
            "salesforce",
            _resp(429, [{"errorCode": "REQUEST_LIMIT_EXCEEDED", "message": "limit"}], {"Retry-After": "7"}),
            "rate_limited",
        ),
        (
            "sap_odata",
            _resp(404, {"error": {"code": "SY/530", "message": {"value": "not found"}}}),
            "not_found",
        ),
        ("sap_odata", _resp(503, {"error": {"code": "", "message": {"value": "down"}}}), "transient"),
        ("servicenow", _resp(401, {"error": {"message": "User Not Authenticated"}}), "auth_expired"),
        ("jira", _resp(400, {"errorMessages": ["bad"], "errors": {}}), "validation"),
        ("dataverse", _resp(412, {"error": {"code": "0x80060882", "message": "precondition"}}), "conflict"),
        ("workday", _resp(500, {"error": "boom"}), "transient"),
    ],
)
def test_error_taxonomy(pack_name, resp, expected):
    err = packs()[pack_name].map_error(resp)
    assert (
        isinstance(err, ConnectorError) and err.error_class == expected and err.error_class in ERROR_CLASSES
    )


def test_rate_limit_hints_are_parsed():
    sf = packs()["salesforce"].hint(httpx.Headers({"Sforce-Limit-Info": "api-usage=9600/10000"}))
    assert (sf.remaining, sf.limit) == (400, 10000) and sf.low  # under 5% left
    sn = packs()["servicenow"].hint(
        httpx.Headers({"X-RateLimit-Limit": "100", "X-RateLimit-Remaining": "80"})
    )
    assert (sn.remaining, sn.limit) == (80, 100) and not sn.low


@pytest.mark.parametrize(
    "pack_name,op,args",
    [
        ("sap_odata", "get_purchase_order", {"po_id": "4500009001"}),
        ("servicenow", "list_incidents", {"category": "integration"}),
        ("workday", "get_worker", {"worker_id": "W-100"}),
    ],
)
async def test_auth_adapter_refreshes_once_on_401(pack_name, op, args):
    pack = packs()[pack_name]
    ctx = await ctx_for()
    await pack.invoke(op, args, ctx)  # warm the token cache
    cached = list(pack.auth.cache._c)
    assert cached, "adapter should cache the vendor token"
    for key in cached:
        pack.auth.cache.put(key, "revoked-token", 3600)  # vendor revoked it; we don't know yet
    res = await pack.invoke(op, args, ctx)
    assert res.data
    assert all(pack.auth.cache.get(k) != "revoked-token" for k in cached)


async def test_sap_writes_fetch_a_csrf_token_and_send_repeatability_id():
    from aiip.fakesaas import state

    tok = await agent_token("bpm-invoice-orchestrator", TOOL_GW)
    await tool(
        tok,
        "erp.park_invoice",
        {
            "po_id": "4500009002",
            "supplier_id": "V-51",
            "amount": "1200.00",
            "tax_code": "V1",
            "vendor_invoice_no": "INV-8200",
        },
        key="csrf-check-01",
    )
    assert state.DATA["sap"]["csrf"]  # a token was fetched before the POST
    assert any(k.startswith("ParkInvoice:") for k in state.DATA["sap"]["idempotency"])


async def test_business_error_inside_http_200_is_a_business_reject():
    pack, op = resolve("sap_odata.park_invoice")
    ctx = await ctx_for(key="bad-tax-0001")
    with pytest.raises(ConnectorError) as e:
        await pack.invoke(
            op,
            {
                "po_id": "4500009002",
                "supplier_id": "V-51",
                "amount": "1.00",
                "tax_code": "V9",
                "vendor_invoice_no": "INV-8300",
            },
            ctx,
        )
    assert e.value.error_class == "business_reject" and e.value.vendor_code == "FTAX/002"


async def test_sandbox_state_admin_is_read_only_view():
    s, body = await call("fake-saas", "GET", "/_admin/state/sap")
    assert s == 200 and "csrf" not in body and "idempotency" not in body
