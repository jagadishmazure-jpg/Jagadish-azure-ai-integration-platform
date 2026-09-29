"""Section 35 - Integration observability: spans with system / operation / business_key /
result_class, HTTP 200 + business error counted as failure, process-completion and identity-mix
metrics, queue depth, and the dashboards that chart them."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.conftest import agent_token, obo_token, tool

from aiip.identity.registrations import TOOL_GW
from aiip.shared import telemetry

ROOT = Path(__file__).resolve().parents[1]
PARK = {
    "po_id": "4500009002",
    "supplier_id": "V-51",
    "amount": "1200.00",
    "tax_code": "V9",
    "vendor_invoice_no": "INV-9100",
}


def _spans(name_prefix: str):
    return [s for s in telemetry.finished_spans() if s.name.startswith(name_prefix)]


async def test_span_carries_integration_attributes():
    alice = await obo_token("alice", "crm-agent", TOOL_GW)
    await tool(
        alice,
        "crm.get_account",
        {"account_id": "ACC-1001"},
        traceparent="00-" + "e" * 32 + "-" + "f" * 16 + "-01",
    )
    span = _spans("tool crm.get_account")[-1]
    a = span.attributes
    assert a["integration.system"] == "salesforce" and a["integration.operation"] == "crm.get_account"
    assert a["integration.business_key"] == "ACC-1001" and a["integration.result_class"] == "ok"
    assert a["integration.identity_mode"] == "obo" and a["integration.subject"] == "alice@contoso.example"
    assert format(span.context.trace_id, "032x") == "e" * 32  # joined the caller's trace


async def test_http_200_with_business_error_is_recorded_as_failure():
    tok = await agent_token("bpm-invoice-orchestrator", TOOL_GW)
    s, _ = await tool(tok, "erp.park_invoice", PARK, key="obs-reject-01")
    assert s == 422
    span = _spans("tool erp.park_invoice")[-1]
    assert span.attributes["integration.result_class"] == "business_reject"
    assert span.attributes["integration.vendor_code"] == "FTAX/002"
    assert span.status.status_code.name == "ERROR"
    sap = telemetry.LEDGER.summary("tool-gateway")["success_by_system"]["sap"]
    assert sap["ok"] == 0 and sap["by_result"]["business_reject"] == 1


async def test_result_classes_cover_timeout_and_authz_deny(monkeypatch):
    from tests.conftest import fast_timeout, fault

    fast_timeout(monkeypatch, "erp.get_sales_order")
    bob = await obo_token("bob", "crm-agent", TOOL_GW)
    await tool(bob, "crm.get_account", {"account_id": "ACC-1001"})
    worker = await agent_token("worker-order-events", TOOL_GW)
    await fault("sap", "slow", count=1, delay_s=2.0)
    await tool(worker, "erp.get_sales_order", {"order_id": "4500002"})
    classes = {s.attributes["integration.result_class"] for s in _spans("tool ")}
    assert {"authz_deny", "timeout"} <= classes


def _metric_points(name: str):
    data = telemetry.metric_data()
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == name:
                    return list(m.data.data_points)
    return []


async def test_metrics_for_calls_identity_mix_and_process_completions():
    alice = await obo_token("alice", "crm-agent", TOOL_GW)
    await tool(alice, "crm.get_account", {"account_id": "ACC-1001"})
    worker = await agent_token("worker-order-events", TOOL_GW)
    await tool(worker, "erp.get_sales_order", {"order_id": "4500001"})
    telemetry.record_process("vendor-invoice", "completed")
    calls = _metric_points("integration.calls")
    assert any(
        p.attributes.get("system") == "salesforce" and p.attributes.get("result_class") == "ok" for p in calls
    )
    modes = {p.attributes["identity_mode"] for p in _metric_points("integration.identity_mode")}
    assert {"obo", "agent"} <= modes
    assert any(
        p.attributes == {"process": "vendor-invoice", "outcome": "completed"}
        for p in _metric_points("process.completions")
    )
    assert _metric_points("integration.duration")


async def test_queue_depth_gauge_reports_dead_letter_queues():
    from aiip.events import gateway as ev_gw
    from aiip.events.bus import Message

    ev_gw.BUS.send("shipment-events", Message("m", {}))
    m = ev_gw.BUS.receive("shipment-events")[0]
    ev_gw.BUS.dead_letter("shipment-events", m.lock_token, "not_found")
    points = {p.attributes["queue"]: p.value for p in _metric_points("queue.depth")}
    assert points["shipment-events/$deadletterqueue"] == 1


def test_dashboards_are_generated_from_the_kql_and_chart_the_required_views():
    r = subprocess.run(
        [sys.executable, "scripts/build_dashboards.py", "--check"], cwd=ROOT, capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout
    wb = json.loads((ROOT / "observability" / "azure-monitor-workbook.json").read_text())
    gf = json.loads((ROOT / "observability" / "grafana-dashboard.json").read_text())
    names = {i["name"] for i in wb["items"]}
    assert {
        "poison_queue_depth",
        "success_by_system",
        "p95_by_tool",
        "identity_mix",
        "process_completions",
    } <= names
    assert len(gf["panels"]) == len(wb["items"]) - 1
    assert all(
        "integration." in json.dumps(p["targets"])
        or "queue" in json.dumps(p["targets"])
        or "DeadletteredMessages" in json.dumps(p["targets"])
        or "process" in json.dumps(p["targets"])
        for p in gf["panels"]
    )
