"""Section 28 - Tool Gateway: registry, allow-lists, side-effect classes, rate limit, cache,
circuit breaker, schema validation, idempotency, timeouts, sanitized errors, audit."""

from __future__ import annotations

import pytest
from tests.conftest import agent_token, call, fast_timeout, fault, obo_token, tool, user_token

from aiip.identity.registrations import TOOL_GW
from aiip.shared.resilience import TokenBucket
from aiip.tools import gateway as gw
from aiip.tools.registry import TOOLS, validate_registry

PARK = {
    "po_id": "4500009002",
    "supplier_id": "V-51",
    "amount": "1200.00",
    "tax_code": "V1",
    "vendor_invoice_no": "INV-5001",
}


def test_registry_is_valid_and_declares_policy_for_every_tool():
    assert validate_registry() == []
    assert {t.side_effect for t in TOOLS.values()} == {"read", "simulate", "commit"}
    for t in TOOLS.values():
        assert t.business_key in t.input_schema["properties"], t.name
        assert t.timeout_s > 0
        if t.side_effect != "read":
            assert t.cache_ttl_s == 0, f"{t.name} must never be cached"


async def test_catalog_is_filtered_by_the_callers_agent_card():
    tok = await agent_token("ap-invoice-agent", TOOL_GW)
    s, body = await call("tool-gateway", "GET", "/v1/tools", tok)
    assert s == 200
    assert [t["name"] for t in body["tools"]] == ["erp.get_purchase_order"]
    s, _ = await call("tool-gateway", "GET", "/v1/tools/erp.park_invoice", tok)
    assert s == 404  # tools outside the card are invisible, not just forbidden


async def test_allow_list_denies_tools_outside_the_card():
    tok = await agent_token("ap-invoice-agent", TOOL_GW)
    s, body = await tool(tok, "erp.park_invoice", PARK, key="k-allowlist-1")
    assert s == 403 and body["error"]["code"] == "authz_deny"


async def test_identity_policy_user_required_and_agent_only():
    worker = await agent_token("worker-order-events", TOOL_GW)
    s, body = await tool(worker, "service.get_entitlement", {"account_number": "ACC-1001"})
    assert s == 403  # not on its card, and user_required anyway
    alice = await obo_token("alice", "crm-agent", TOOL_GW)
    s, body = await tool(alice, "service.get_entitlement", {"account_number": "ACC-1001"})
    assert s == 200, body


async def test_simulate_does_not_persist_anything():
    tok = await agent_token("worker-shipment-events", TOOL_GW)
    _, before = await call("fake-saas", "GET", "/_admin/state/sap")
    s, body = await tool(
        tok,
        "erp.simulate_sales_order",
        {"customer_ref": "ACC-1001", "items": [{"material": "MAT-PALLET-02", "quantity": 2}]},
    )
    assert s == 200 and float(body["data"]["net_amount"]) == 70.0
    _, after = await call("fake-saas", "GET", "/_admin/state/sap")
    assert len(after["sales_orders"]) == len(before["sales_orders"])


async def test_commit_requires_idempotency_key_and_replays_on_retry():
    tok = await agent_token("bpm-invoice-orchestrator", TOOL_GW)
    s, body = await tool(tok, "erp.park_invoice", PARK)
    assert s == 400 and "Idempotency-Key" in body["error"]["message"]
    s1, b1 = await tool(tok, "erp.park_invoice", PARK, key="park-INV-5001")
    s2, b2 = await tool(tok, "erp.park_invoice", PARK, key="park-INV-5001")
    assert s1 == s2 == 200
    assert b2["replayed"] and b2["replay_source"] == "gateway"
    assert b1["data"]["invoice_document"] == b2["data"]["invoice_document"]
    s3, b3 = await tool(tok, "erp.park_invoice", {**PARK, "amount": "1300.00"}, key="park-INV-5001")
    assert s3 == 409 and b3["error"]["code"] == "conflict"


async def test_idempotency_survives_gateway_restart_via_vendor_repeatability():
    tok = await agent_token("bpm-invoice-orchestrator", TOOL_GW)
    _, _b1 = await tool(tok, "erp.park_invoice", PARK, key="park-restart-1")
    gw.IDEMPOTENCY.clear()  # gateway restarted; its store is gone
    s, b2 = await tool(tok, "erp.park_invoice", PARK, key="park-restart-1")
    assert s == 200 and b2["replay_source"] == "vendor"
    _, sap = await call("fake-saas", "GET", "/_admin/state/sap")
    assert sum("INV-5001" in inv.values() for inv in sap["invoices"].values()) == 1


async def test_input_schema_is_enforced_before_any_vendor_call():
    tok = await agent_token("worker-order-events", TOOL_GW)
    s, body = await tool(tok, "erp.get_sales_order", {"order_id": "12; DROP TABLE"})
    assert s == 400 and body["error"]["code"] == "validation_error"
    s, body = await tool(tok, "erp.get_sales_order", {"order_id": "4500001", "extra": 1})
    assert s == 400


async def test_output_schema_violation_is_a_failure_not_passed_to_the_model(monkeypatch):
    from aiip.connectors.base import ConnectorResult
    from aiip.connectors.registry import resolve

    pack, _ = resolve("sap_odata.get_sales_order")

    async def bad(args, ctx):
        return ConnectorResult({"order_id": 42, "unexpected": "x"})

    monkeypatch.setitem(pack.operations, "get_sales_order", bad)
    tok = await agent_token("worker-order-events", TOOL_GW)
    s, body = await tool(tok, "erp.get_sales_order", {"order_id": "4500001"})
    assert s == 500 and body["error"]["code"] == "internal_error"
    assert "unexpected" not in str(body["error"].get("message"))


async def test_safe_reads_are_cached_per_subject():
    tok = await agent_token("worker-order-events", TOOL_GW)
    _, b1 = await tool(tok, "erp.get_sales_order", {"order_id": "4500001"})
    _, b2 = await tool(tok, "erp.get_sales_order", {"order_id": "4500001"})
    assert (b1["cache"], b2["cache"]) == ("miss", "hit")
    alice = await obo_token("alice", "crm-agent", TOOL_GW)
    bob = await obo_token("bob", "crm-agent", TOOL_GW)
    s_a, _ = await tool(alice, "crm.get_account", {"account_id": "ACC-1001"})
    s_b, _ = await tool(bob, "crm.get_account", {"account_id": "ACC-1001"})
    assert (s_a, s_b) == (200, 403)  # alice's cached record is never served to bob


async def test_per_tenant_rate_limit(monkeypatch):
    monkeypatch.setattr(gw, "RATE", TokenBucket(capacity=2, refill_per_s=0.001))
    contoso = await agent_token("worker-order-events", TOOL_GW, "contoso")
    fabrikam = await agent_token("worker-order-events", TOOL_GW, "fabrikam")
    codes = [(await tool(contoso, "erp.get_sales_order", {"order_id": "4500001"}))[0] for _ in range(3)]
    assert codes == [200, 200, 429]
    s, _ = await tool(fabrikam, "erp.get_sales_order", {"order_id": "4500002"})
    assert s != 429  # other tenant has its own bucket


async def test_circuit_breaker_opens_and_fails_fast():
    tok = await agent_token("bpm-invoice-orchestrator", TOOL_GW)
    await fault("sap", "error")
    results = [await tool(tok, "erp.get_purchase_order", {"po_id": "4500009001"}) for _ in range(4)]
    assert [s for s, _ in results] == [503] * 4
    assert "circuit open" in results[3][1]["error"]["message"]
    assert gw.BREAKERS["sap"].state == "open"


async def test_business_reject_does_not_trip_the_breaker():
    tok = await agent_token("bpm-invoice-orchestrator", TOOL_GW)
    for i in range(4):
        s, body = await tool(
            tok,
            "erp.park_invoice",
            {**PARK, "tax_code": "V9", "vendor_invoice_no": f"INV-60{i}0"},
            key=f"reject-key-{i}",
        )
        assert s == 422 and body["error"]["code"] == "business_reject"
        assert body["error"]["detail"]["vendor_code"] == "FTAX/002"
    assert gw.BREAKERS["sap"].state == "closed"


async def test_timeout_is_enforced_and_classified(monkeypatch):
    fast_timeout(monkeypatch, "erp.get_sales_order")
    tok = await agent_token("worker-order-events", TOOL_GW)
    await fault("sap", "slow", count=1, delay_s=2.0)
    s, body = await tool(tok, "erp.get_sales_order", {"order_id": "4500002"})
    assert s == 504 and body["error"]["code"] == "timeout"


async def test_vendor_rate_limit_becomes_backoff_hint():
    tok = await agent_token("worker-order-events", TOOL_GW)
    await fault("sap", "rate_limit", count=1)
    s, body = await tool(tok, "erp.get_sales_order", {"order_id": "4500002"})
    assert s == 429 and body["error"]["code"] == "rate_limited"
    s, body = await tool(tok, "erp.get_sales_order", {"order_id": "4500003"})
    assert s == 429 and "back off" in body["error"]["message"]


async def test_errors_are_sanitized(monkeypatch):
    from aiip.connectors.registry import resolve

    pack, _ = resolve("sap_odata.get_sales_order")

    async def boom(args, ctx):
        raise RuntimeError("password=hunter2 at /opt/app/secret.py line 12")

    monkeypatch.setitem(pack.operations, "get_sales_order", boom)
    tok = await agent_token("worker-order-events", TOOL_GW)
    s, body = await tool(tok, "erp.get_sales_order", {"order_id": "4500001"})
    assert s == 500
    text = str(body)
    assert "hunter2" not in text and "Traceback" not in text and "secret.py" not in text
    assert body["error"]["correlation_id"]


async def test_audit_answers_who_did_what_to_which_business_key_when():
    alice = await obo_token("alice", "crm-agent", TOOL_GW)
    s, _ = await tool(
        alice, "crm.upsert_case", {"account_id": "ACC-1001", "subject": "Damaged pallet"}, key="case-key-0001"
    )
    assert s == 200
    s, body = await call("tool-gateway", "GET", "/v1/audit", alice, params={"business_key": "ACC-1001"})
    rec = body["records"][-1]
    assert rec["actor"] == "crm-agent" and rec["subject"] == "alice@contoso.example"
    assert rec["operation"] == "crm.upsert_case" and rec["business_key"] == "ACC-1001" and rec["ts"]
    assert rec["identity_mode"] == "obo" and body["chain_ok"]
    gw.AUDIT.records[0]["business_key"] = "tampered"
    _, body = await call("tool-gateway", "GET", "/v1/audit", alice)
    assert body["chain_ok"] is False


@pytest.mark.parametrize("missing", ["authorization"])
async def test_requires_a_valid_token(missing):
    s, _body = await tool("not-a-jwt", "erp.get_sales_order", {"order_id": "4500001"})
    assert s == 401


async def _park(tok, amount: str, po: str, supplier: str, inv: str) -> str:
    s, b = await tool(
        tok,
        "erp.park_invoice",
        {"po_id": po, "supplier_id": supplier, "amount": amount, "tax_code": "V1", "vendor_invoice_no": inv},
        key=f"park-{inv}",
    )
    assert s == 200, b
    return b["data"]["invoice_document"]


async def test_hitl_threshold_and_understated_amount_is_caught_by_sap():
    tok = await agent_token("bpm-invoice-orchestrator", TOOL_GW)
    small = await _park(tok, "1200.00", "4500009002", "V-51", "INV-7001")
    s, b = await tool(
        tok, "erp.post_parked_invoice", {"invoice_document": small, "amount": "1200.00"}, key="post-INV-7001"
    )
    assert s == 200 and b["data"]["status"] == "POSTED"  # under 10,000.00: straight-through
    big = await _park(tok, "12000.00", "4500009001", "V-44", "INV-7002")
    s, b = await tool(
        tok, "erp.post_parked_invoice", {"invoice_document": big, "amount": "12000.00"}, key="post-INV-7002"
    )
    assert s == 428
    # lying about the amount to dodge approval: SAP compares it with the parked document
    s, b = await tool(
        tok,
        "erp.post_parked_invoice",
        {"invoice_document": big, "amount": "1200.00"},
        key="post-INV-7002-lie",
    )
    assert s == 422 and b["error"]["detail"]["vendor_code"] == "M8/108"


async def test_approval_is_single_use_bound_to_args_and_needs_the_right_human():
    tok = await agent_token("bpm-invoice-orchestrator", TOOL_GW)
    big = await _park(tok, "12000.00", "4500009001", "V-44", "INV-7003")
    args = {"invoice_document": big, "amount": "12000.00"}
    s, req = await call(
        "tool-gateway", "POST", "/v1/approvals", tok, json={"tool": "erp.post_parked_invoice", "args": args}
    )
    assert s == 200 and req["approver_role"] == "APApprover"
    alice = await user_token("alice", TOOL_GW)
    s, _ = await call(
        "tool-gateway", "POST", f"/v1/approvals/{req['approval_id']}/decision", alice, json={"approved": True}
    )
    assert s == 403  # CareRep cannot approve AP postings
    s, _ = await call(
        "tool-gateway", "POST", f"/v1/approvals/{req['approval_id']}/decision", tok, json={"approved": True}
    )
    assert s == 403  # an agent cannot approve its own commit
    carol = await user_token("carol", TOOL_GW)
    s, _ = await call(
        "tool-gateway", "POST", f"/v1/approvals/{req['approval_id']}/decision", carol, json={"approved": True}
    )
    assert s == 200
    s, b = await tool(tok, "erp.post_parked_invoice", args, key="post-INV-7003", approval=req["approval_id"])
    assert s == 200 and b["approved_by"] == "carol@contoso.example"
    other = await _park(tok, "12000.00", "4500009001", "V-44", "INV-7004")
    s, _ = await tool(
        tok,
        "erp.post_parked_invoice",
        {"invoice_document": other, "amount": "12000.00"},
        key="post-INV-7004",
        approval=req["approval_id"],
    )
    assert s == 403  # approval was for a different document
