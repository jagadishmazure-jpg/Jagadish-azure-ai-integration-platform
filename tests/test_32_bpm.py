"""Section 32 - BPM + agent: Durable orchestration owns the money/compliance process, calls the
agent as activities, waits for a human with a timer, compensates, maps process -> graph runs.
The orchestrator is also replayed by the real azure-functions-durable engine."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import azure.durable_functions as df
from azure.durable_functions.orchestrator import Orchestrator
from tests.conftest import call, user_token

from aiip.bpm.activities import ACTIVITIES
from aiip.bpm.orchestration import vendor_invoice
from aiip.identity.registrations import TOOL_GW

ROOT = Path(__file__).resolve().parents[1]
REVIEW = "ACME Metals\nInvoice No: INV-7781\nVendor ID: V-44\nPO: 4500009001\nTotal due: USD 12,000.00\nTax code: V1"
AUTO = "Fastener Co\nInvoice No: INV-3100\nVendor ID: V-51\nPO: 4500009002\nTotal due: USD 1,200.00\nTax code: V1"
WEBHOOK = "/runtime/webhooks/durabletask/instances"


async def start(text: str) -> dict:
    s, r = await call(
        "bpm-host",
        "POST",
        "/api/orchestrators/vendor_invoice",
        json={"invoice_text": text, "tenant": "contoso"},
    )
    assert s == 202
    _, st = await call("bpm-host", "GET", f"{WEBHOOK}/{r['id']}")
    return st


async def sap_status(doc: str) -> str:
    _, sap = await call("fake-saas", "GET", "/_admin/state/sap")
    return sap["invoices"][doc]["Status"]


async def test_auto_route_completes_without_a_human():
    st = await start(AUTO)
    assert st["runtimeStatus"] == "Completed" and st["output"]["outcome"] == "completed"
    assert await sap_status(st["output"]["invoice_document"]) == "PAYMENT_SCHEDULED"
    assert "payment is scheduled" in st["output"]["note"]


async def test_review_route_waits_then_completes_after_real_approval():
    st = await start(REVIEW)
    cs = st["customStatus"]
    assert st["runtimeStatus"] == "Running" and cs["stage"] == "awaiting_approval"
    assert cs["approver"] == "carol@contoso.example"
    carol = await user_token("carol", TOOL_GW)
    s, _d = await call(
        "tool-gateway", "POST", f"/v1/approvals/{cs['approval_id']}/decision", carol, json={"approved": True}
    )
    assert s == 200
    await call(
        "bpm-host",
        "POST",
        f"{WEBHOOK}/{st['instanceId']}/raiseEvent/ApprovalDecision",
        json={"approved": True, "approval_id": cs["approval_id"]},
    )
    _, st = await call("bpm-host", "GET", f"{WEBHOOK}/{st['instanceId']}")
    assert st["output"]["outcome"] == "completed" and st["output"]["approved_by"] == "carol@contoso.example"
    assert st["replays"] >= 1


async def test_approver_rejection_compensates():
    st = await start(REVIEW)
    cs = st["customStatus"]
    carol = await user_token("carol", TOOL_GW)
    await call(
        "tool-gateway", "POST", f"/v1/approvals/{cs['approval_id']}/decision", carol, json={"approved": False}
    )
    await call(
        "bpm-host",
        "POST",
        f"{WEBHOOK}/{st['instanceId']}/raiseEvent/ApprovalDecision",
        json={"approved": False, "approval_id": cs["approval_id"]},
    )
    _, st = await call("bpm-host", "GET", f"{WEBHOOK}/{st['instanceId']}")
    assert st["output"]["outcome"] == "rejected_by_approver"
    assert await sap_status(cs["invoice_document"]) == "DELETED"


async def test_timer_timeout_compensates():
    st = await start(REVIEW)
    doc = st["customStatus"]["invoice_document"]
    _, st2 = await call(
        "bpm-host", "POST", f"/_local/instances/{st['instanceId']}/advance", params={"hours": 47}
    )
    assert st2["runtimeStatus"] == "Running"
    _, st2 = await call(
        "bpm-host", "POST", f"/_local/instances/{st['instanceId']}/advance", params={"hours": 2}
    )
    assert st2["output"]["outcome"] == "timed_out_compensated"
    assert await sap_status(doc) == "DELETED"


async def test_forged_approval_event_cannot_post_money():
    st = await start(REVIEW)
    cs = st["customStatus"]
    # someone raises the event directly, without a human decision at the Tool Gateway
    await call(
        "bpm-host",
        "POST",
        f"{WEBHOOK}/{st['instanceId']}/raiseEvent/ApprovalDecision",
        json={"approved": True, "approval_id": cs["approval_id"]},
    )
    _, st = await call("bpm-host", "GET", f"{WEBHOOK}/{st['instanceId']}")
    assert st["output"]["outcome"] == "post_failed_approval_required_compensated"
    assert await sap_status(cs["invoice_document"]) == "DELETED"


async def test_business_reject_from_sap_ends_the_process_cleanly():
    st = await start(AUTO.replace("Tax code: V1", "Tax code: V9"))
    assert st["output"]["outcome"] == "business_reject" and "V9" in st["output"]["message"]


async def test_incomplete_invoice_is_rejected_before_any_commit():
    st = await start("Invoice No: INV-4000\nTotal due: USD 10.00")
    assert st["output"]["outcome"] == "rejected_incomplete" and "po_id" in st["output"]["missing"]
    _, sap = await call("fake-saas", "GET", "/_admin/state/sap")
    assert sap["invoices"] == {}


async def test_process_id_maps_to_graph_run_ids():
    st = await start(AUTO)
    s, pm = await call("bpm-host", "GET", f"/v1/process-map/{st['instanceId']}")
    assert s == 200 and pm["runs"][0].startswith("graph-run-") and pm["outcome"] == "completed"
    assert st["output"]["run_id"] == pm["runs"][0]


async def test_activity_retry_does_not_double_post():
    payload = {
        "process_id": "p-1",
        "run_id": "r-1",
        "tenant": "contoso",
        "step": "park",
        "tool": "erp.park_invoice",
        "args": {
            "po_id": "4500009002",
            "supplier_id": "V-51",
            "amount": "1200.00",
            "tax_code": "V1",
            "vendor_invoice_no": "INV-3200",
        },
    }
    a = await ACTIVITIES["sap_commit"](payload)
    b = await ACTIVITIES["sap_commit"](payload)  # Durable retries an activity after a host crash
    assert a["ok"] and b["ok"] and a["data"]["invoice_document"] == b["data"]["invoice_document"]


# ------------------------------------------------------------ real Durable Functions replay engine
def _ctx(history: list[dict], inp: dict) -> df.DurableOrchestrationContext:
    return df.DurableOrchestrationContext.from_json(
        json.dumps(
            {
                "history": history,
                "input": json.dumps(inp),
                "instanceId": "sdk-replay-1",
                "isReplaying": False,
                "parentInstanceId": None,
            }
        )
    )


async def test_orchestrator_runs_on_the_real_durable_sdk_replay_engine():
    """Drive `vendor_invoice` with azure-functions-durable's own Orchestrator: each episode replays
    the history, we execute the newly scheduled activity for real and append its result."""
    ts = "2026-09-25T12:00:00.000000Z"
    inp = {"invoice_text": AUTO, "tenant": "contoso"}
    history = [
        {"EventType": 12, "EventId": -1, "IsPlayed": False, "Timestamp": ts},
        {
            "EventType": 0,
            "EventId": -1,
            "Input": json.dumps(inp),
            "Name": "vendor_invoice",
            "Version": "",
            "IsPlayed": False,
            "Timestamp": ts,
        },
    ]
    executed = []
    for episode in range(20):
        out = json.loads(Orchestrator(vendor_invoice).handle(_ctx(history, inp)))
        if out["isDone"]:
            break
        action = out["actions"][-1][0]
        assert action["actionType"] == 0  # CallActivity
        name, arg = action["functionName"], json.loads(action["input"])
        result = await ACTIVITIES[name](arg)
        executed.append(name)
        history += [
            {"EventType": 4, "EventId": episode, "Name": name, "IsPlayed": False, "Timestamp": ts},
            {
                "EventType": 5,
                "EventId": -1,
                "TaskScheduledId": episode,
                "Result": json.dumps(result),
                "IsPlayed": False,
                "Timestamp": ts,
            },
        ]
    assert out["isDone"] and out["output"]["outcome"] == "completed"
    assert executed == [
        "register_run",
        "agent_extract",
        "agent_classify",
        "sap_commit",
        "sap_commit",
        "sap_commit",
        "agent_draft",
        "record_outcome",
    ]


def test_review_path_schedules_timer_and_external_event_on_real_sdk():
    ts = "2026-09-25T12:00:00.000000Z"
    inp = {"invoice_text": REVIEW, "tenant": "contoso"}
    results = [
        ("register_run", {"run_id": "graph-run-x"}),
        (
            "agent_extract",
            {
                "ok": True,
                "result": {
                    "invoice": {
                        "vendor_invoice_no": "INV-7781",
                        "po_id": "4500009001",
                        "supplier_id": "V-44",
                        "amount": "12000.00",
                        "tax_code": "V1",
                    },
                    "missing": [],
                },
            },
        ),
        (
            "agent_classify",
            {"ok": True, "result": {"route": "review", "reason": "above threshold", "cost_center": "CC-100"}},
        ),
        ("sap_commit", {"ok": True, "data": {"invoice_document": "5105600001"}}),
        ("find_approver", {"approver": {"email": "carol@contoso.example"}}),
        ("request_approval", {"approval_id": "apr-1"}),
    ]
    history = [
        {"EventType": 12, "EventId": -1, "IsPlayed": False, "Timestamp": ts},
        {
            "EventType": 0,
            "EventId": -1,
            "Input": json.dumps(inp),
            "Name": "vendor_invoice",
            "Version": "",
            "IsPlayed": False,
            "Timestamp": ts,
        },
    ]
    for i, (name, res) in enumerate(results):
        history += [
            {"EventType": 4, "EventId": i, "Name": name, "IsPlayed": False, "Timestamp": ts},
            {
                "EventType": 5,
                "EventId": -1,
                "TaskScheduledId": i,
                "Result": json.dumps(res),
                "IsPlayed": False,
                "Timestamp": ts,
            },
        ]
    out = json.loads(Orchestrator(vendor_invoice).handle(_ctx(history, inp)))
    last = out["actions"][-1]
    kinds = sorted(a["actionType"] for a in last)
    assert not out["isDone"] and kinds == [5, 6]  # CreateTimer + WaitForExternalEvent, raced by task_any
    timer = next(a for a in last if a["actionType"] == 5)
    assert timer["fireAt"].startswith("2026-09-27T12:00:00")  # 48h after the orchestration clock
    assert out["customStatus"]["stage"] == "awaiting_approval"


def test_function_app_registers_orchestrator_activities_and_http_routes():
    sys.path.insert(0, str(ROOT / "functions"))
    try:
        import function_app
    finally:
        sys.path.pop(0)
    fns = {
        f.get_function_name(): [b.type for b in f.get_bindings()] for f in function_app.app.get_functions()
    }
    assert fns["vendor_invoice"] == ["orchestrationTrigger"]
    for name in ACTIVITIES:
        assert fns[name] == ["activityTrigger"], name
    assert "durableClient" in fns["http_start"] and "durableClient" in fns["approval_callback"]


def test_logic_apps_alternative_has_timeout_and_compensation():
    wf = json.loads((ROOT / "logicapps" / "vendor-invoice" / "workflow.json").read_text())
    actions = wf["definition"]["actions"]
    assert (
        actions["Wait_for_approval"]["type"] == "HttpWebhook"
        and actions["Wait_for_approval"]["limit"]["timeout"] == "PT48H"
    )
    comp = actions["Approved"]["actions"]["Compensate_reverse"]
    assert comp["runAfter"]["Post_and_pay"] == ["Failed", "TimedOut"]
    assert actions["Park_invoice"]["inputs"]["headers"]["Idempotency-Key"]
    assert wf["kind"] == "Stateful"
