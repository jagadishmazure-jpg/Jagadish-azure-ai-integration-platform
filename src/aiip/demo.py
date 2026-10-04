"""End-to-end demo of the integration plane.

    python scripts/demo.py            # full topology: 15 processes over real HTTP on 127.0.0.1
    python scripts/demo.py --inproc   # same scenarios, services called in-process (fast, used by tests)

Scenarios: interactive OBO journey (and the ACL difference between two users), idempotent retry
(including across a gateway restart), business_reject (HTTP 200 carrying a SAP error), authz_deny,
circuit-breaker trip, MCP governance (read-only SQL, PII refusal, injection screening, write needs
HITL), event-driven runs (dedup, storm budget, dead-letter), and a HITL BPM run plus a timed-out run
that compensates. Every number printed comes from this run."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

RESULTS: dict[str, Any] = {}


def say(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 70 - len(title)))


def ok(label: str, detail: Any = "") -> None:
    print(f"  [ok] {label}" + (f": {detail}" if detail != "" else ""))


def expect(cond: bool, label: str) -> None:
    if not cond:
        raise AssertionError(f"demo expectation failed: {label}")


async def _json(service: str, method: str, path: str, **kw) -> tuple[int, Any]:
    from aiip.shared import http

    async with http.client(service, timeout=30) as c:
        r = await c.request(method, path, **kw)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text


async def _tool(
    token: str, name: str, args: dict, key: str | None = None, approval: str | None = None
) -> tuple[int, dict]:
    h = {"authorization": f"Bearer {token}"}
    if key:
        h["idempotency-key"] = key
    if approval:
        h["x-approval-id"] = approval
    return await _json("tool-gateway", "POST", f"/v1/tools/{name}/invoke", json={"args": args}, headers=h)


async def obo_journey() -> None:
    from aiip.a2a import client as a2a
    from aiip.config import TENANTS
    from aiip.identity.client import login
    from aiip.identity.registrations import agent_uri
    from aiip.shared import errors as E

    say("1. Interactive journey, user-scoped (OBO): planner fans out over A2A")
    ask = {
        "account_number": "ACC-1001",
        "order_id": "4500001",
        "question": "Where is my order?",
        "open_case": True,
    }
    alice = await login("alice", agent_uri("care-planner"))
    t0 = time.perf_counter()
    out = await a2a.send(
        "care-planner",
        "resolve_customer_request",
        ask,
        token=alice,
        tenant_id=TENANTS["contoso"],
        timeout_s=60,
    )
    ms = (time.perf_counter() - t0) * 1000
    res = out["output"]
    expect(res["result_class"] == "ok" and res["case"] is not None, "alice gets a full answer")
    ok("alice (East care rep)", res["answer"])
    ok("fan-out", f"{res['fan_out']} acting as user {res['acting_as'][:8]}..., {ms:.0f} ms end to end")

    again = await a2a.send(
        "care-planner",
        "resolve_customer_request",
        ask,
        token=alice,
        tenant_id=TENANTS["contoso"],
        timeout_s=60,
    )
    case2 = again["output"]["case"]
    expect(
        case2["replayed"] and case2["case"]["case_id"] == res["case"]["case"]["case_id"],
        "retry replays the case",
    )
    ok("idempotent retry of the whole journey", f"case {case2['case']['case_id']} replayed, not duplicated")

    say("2. authz_deny: same question, different user (record-level ACL in the CRM)")
    bob = await login("bob", agent_uri("care-planner"))
    out = await a2a.send(
        "care-planner",
        "resolve_customer_request",
        {**ask, "open_case": False},
        token=bob,
        tenant_id=TENANTS["contoso"],
        timeout_s=60,
    )
    expect(out["output"]["result_class"] == E.AUTHZ_DENY, "bob is denied")
    ok("bob (West care rep)", out["output"]["answer"])
    dave = await login("dave", agent_uri("care-planner"))
    try:
        await a2a.send(
            "care-planner", "resolve_customer_request", ask, token=dave, tenant_id=TENANTS["contoso"]
        )
        raise AssertionError("cross-tenant call should fail")
    except E.GatewayError as exc:
        expect(exc.code == E.AUTHZ_DENY, "cross-tenant header denied")
        ok("dave (other tenant) spoofing x-tenant-id", f"{exc.code}: {exc.message}")
    RESULTS["obo"] = {"alice_ms": round(ms), "case": res["case"]["case"]["case_id"]}


async def tool_drills() -> None:
    from aiip.identity.client import IdentityClient
    from aiip.identity.registrations import TOOL_GW

    orch = IdentityClient("bpm-invoice-orchestrator")
    token = await orch.agent_token(TOOL_GW)

    say("3. Idempotent commit: retry with the same key, then again after a gateway restart")
    args = {
        "po_id": "4500009002",
        "supplier_id": "V-51",
        "amount": "1200.00",
        "tax_code": "V1",
        "vendor_invoice_no": "INV-9001",
    }
    s1, r1 = await _tool(token, "erp.park_invoice", args, key="demo-park-INV-9001")
    s2, r2 = await _tool(token, "erp.park_invoice", args, key="demo-park-INV-9001")
    expect(
        s1 == 200 and s2 == 200 and r1["data"]["invoice_document"] == r2["data"]["invoice_document"],
        "same document",
    )
    ok("first call", f"parked as {r1['data']['invoice_document']}")
    ok("retry, same key", f"replay_source={r2.get('replay_source')}")
    await _json("tool-gateway", "POST", "/v1/admin/reset", params={"scope": "restart"})
    s3, r3 = await _tool(token, "erp.park_invoice", args, key="demo-park-INV-9001")
    _, sap = await _json("fake-saas", "GET", "/_admin/state/sap")
    count = sum(1 for i in sap["invoices"].values() if "INV-9001" in i.values())
    expect(
        s3 == 200 and r3["data"]["invoice_document"] == r1["data"]["invoice_document"],
        "SAP repeatability replays",
    )
    ok(
        "retry after gateway restart (idempotency cache lost)",
        f"replay_source={r3.get('replay_source')}; SAP holds {count} invoice(s) for INV-9001",
    )
    RESULTS["idempotency"] = {
        "replay_sources": [r2.get("replay_source"), r3.get("replay_source")],
        "sap_invoices_for_key": count,
    }

    say("4. business_reject: SAP answers HTTP 200 with a BAPI error in the payload")
    s, r = await _tool(
        token,
        "erp.park_invoice",
        {**args, "tax_code": "V9", "vendor_invoice_no": "INV-9002"},
        key="demo-park-INV-9002",
    )
    expect(s == 422 and r["error"]["code"] == "business_reject", "business reject surfaced")
    ok("gateway result", f"HTTP {s} {r['error']['code']}: {r['error']['message']}")
    RESULTS["business_reject"] = r["error"]

    say("5. authz_deny at the Tool Gateway: tool not on the caller's agent card")
    ap = await IdentityClient("ap-invoice-agent").agent_token(TOOL_GW)
    s, r = await _tool(ap, "erp.park_invoice", args, key="demo-ap-park")
    expect(s in (403, 404), "ap-invoice-agent cannot commit")
    ok("ap-invoice-agent -> erp.park_invoice", f"HTTP {s} {r['error']['code']}: {r['error']['message']}")
    s, r = await _tool(
        token,
        "erp.post_parked_invoice",
        {"invoice_document": r1["data"]["invoice_document"], "amount": "12000.00"},
        key="demo-post-no-approval",
    )
    expect(s == 428, "post above threshold needs approval")
    ok("orchestrator posts 12,000.00 without a human approval", f"HTTP {s} {r['error']['code']}")

    say("6. Circuit breaker: SAP starts failing")
    await _json("tool-gateway", "POST", "/v1/admin/reset", params={"scope": "cache"})
    await _json("fake-saas", "POST", "/_admin/faults/sap", json={"mode": "error", "count": 50})
    seen = []
    for _ in range(5):
        t0 = time.perf_counter()
        s, r = await _tool(token, "erp.get_purchase_order", {"po_id": "4500009001"})
        seen.append((s, r.get("error", {}).get("message", ""), round((time.perf_counter() - t0) * 1000)))
    _, br = await _json("tool-gateway", "GET", "/v1/breakers")
    for s, m, ms in seen:
        ok(f"HTTP {s} in {ms} ms", m)
    expect(any("circuit" in m.lower() for _, m, _ in seen), "breaker opened")
    ok("breaker state", {k: v["state"] for k, v in br.items()})
    await _json("fake-saas", "DELETE", "/_admin/faults")
    await _json("tool-gateway", "POST", "/v1/admin/reset", params={"scope": "breakers"})
    s, _ = await _tool(token, "erp.get_purchase_order", {"po_id": "4500009001"})
    expect(s == 200, "recovers")
    ok("faults cleared + breaker reset", f"HTTP {s}")
    RESULTS["breaker"] = {"calls": [x[0] for x in seen], "transitions": br.get("sap", {}).get("transitions")}


async def mcp_drills() -> None:
    from aiip.identity.client import IdentityClient
    from aiip.identity.registrations import MCP_GW

    say("7. MCP Gateway: catalog, read-only SQL, PII refusal, injection screening, HITL writes")
    tok = await IdentityClient("data-agent").agent_token(MCP_GW)
    h = {"authorization": f"Bearer {tok}"}
    _, cat = await _json("mcp-gateway", "GET", "/v1/servers", headers=h)
    ok("catalog", [f"{s['name']} ({s['mode']}, {s['transport']})" for s in cat["servers"]])
    ok("data-agent may use", cat["allowed_for_caller"])
    s, r = await _json(
        "mcp-gateway",
        "POST",
        "/v1/servers/sql-warehouse/tools/run_query/call",
        json={
            "arguments": {
                "sql": "SELECT region, week, on_time_pct FROM delivery_kpis WHERE region = 'east' ORDER BY week"
            }
        },
        headers=h,
    )
    expect(s == 200, "sql ok")
    ok("data-agent SELECT on delivery_kpis", f"{len(r['data']['rows'])} rows")
    s, r = await _json(
        "mcp-gateway",
        "POST",
        "/v1/servers/sql-warehouse/tools/run_query/call",
        json={"arguments": {"sql": "SELECT * FROM customer_pii"}},
        headers=h,
    )
    expect(s != 200 or r.get("data", {}).get("error"), "pii refused")
    ok("SELECT on customer_pii", f"HTTP {s} {json.dumps(r.get('error', r.get('data')))[:120]}")
    w = await IdentityClient("worker-order-events").agent_token(MCP_GW)
    hw = {"authorization": f"Bearer {w}"}
    s, r = await _json(
        "mcp-gateway",
        "POST",
        "/v1/servers/servicenow-incidents/tools/get_incident/call",
        json={"arguments": {"incident_id": "b2c3d4e5f60718293a4b5c6d7e8f9011"}},
        headers=hw,
    )
    flags = r.get("screening", {}).get("flags", [])
    expect(s == 200 and flags, "injection flagged")
    ok("ServiceNow incident with an injected instruction", f"{len(flags)} field(s) withheld: {flags[0]}")
    s, r = await _json(
        "mcp-gateway",
        "POST",
        "/v1/servers/servicenow-incidents/tools/create_incident/call",
        json={
            "arguments": {
                "short_description": "demo",
                "priority": 3,
                "business_key": "DEMO-1",
                "idempotency_key": "demo-mcp-write-0001",
            }
        },
        headers={**hw, "idempotency-key": "demo-mcp-write"},
    )
    expect(s == 428, "mcp write needs approval")
    ok("MCP write without a human approval", f"HTTP {s} {r['error']['code']}")
    RESULTS["mcp"] = {"injection_flags": len(flags)}


async def event_drills() -> None:
    from aiip.config import TENANTS
    from aiip.events.canonical import make_event
    from aiip.events.worker import EventWorker
    from aiip.identity.client import IdentityClient
    from aiip.identity.registrations import EVENT_GW

    say("8. Event-driven agents: SAP events -> Event Gateway -> queue -> worker (agent identity)")
    pub = await IdentityClient("sap-integration-suite").agent_token(EVENT_GW)
    h = {"authorization": f"Bearer {pub}"}
    tid = TENANTS["contoso"]
    evs = [
        make_event(
            "OrderCreated",
            {"order_id": "4500003", "customer_ref": "ACC-1003", "net_amount": "2300.00", "currency": "USD"},
            tenant_id=tid,
        ),
        make_event(
            "OrderCreated",
            {"order_id": "4500003", "customer_ref": "ACC-1003", "net_amount": "2300.00", "currency": "USD"},
            tenant_id=tid,
        ),
        make_event(
            "ShipmentLate", {"delivery_id": "80000001", "order_id": "4500001", "days_late": 7}, tenant_id=tid
        ),
        make_event(
            "ShipmentLate", {"delivery_id": "89999999", "order_id": "4599999", "days_late": 2}, tenant_id=tid
        ),
    ]
    _, r = await _json("event-gateway", "POST", "/v1/events", json=evs, headers=h)
    ok("admission", [f"{x['type']} {x['business_key']}: {x['status']}" for x in r["results"]])
    expect(r["results"][1]["status"] == "duplicate", "dedup on business key")

    storm = [
        make_event(
            "ShipmentLate",
            {"delivery_id": f"8900{i:04d}", "order_id": "4500002", "days_late": 1},
            tenant_id=tid,
        )
        for i in range(8)
    ]
    _, r = await _json("event-gateway", "POST", "/v1/events", json=storm, headers=h)
    outcomes = [x["status"] for x in r["results"]]
    expect("parked_budget" in outcomes, "storm parked")
    ok(
        "event storm (8 ShipmentLate in a burst, budget 5/min per tenant)",
        {o: outcomes.count(o) for o in sorted(set(outcomes))},
    )

    for q in ("order-events", "shipment-events"):
        results = await EventWorker(q).run_once(max_messages=20)
        for x in results:
            ok(
                f"worker {q}",
                {
                    k: v
                    for k, v in x.items()
                    if k in ("status", "outcome", "reason", "result_class", "business_key")
                },
            )
    _, stats = await _json("event-gateway", "GET", "/bus/stats")
    _, comp = await _json("event-gateway", "GET", "/bus/completions/peek")
    ok(
        "completion events emitted",
        [f"{m['body']['type'].split('.')[-3]} {m['body']['subject']}" for m in comp.get("messages", [])],
    )
    ok("queue depths", stats)
    RESULTS["events"] = {"storm": {o: outcomes.count(o) for o in set(outcomes)}, "queues": stats}


async def bpm_drills() -> None:
    from aiip.identity.client import login
    from aiip.identity.registrations import TOOL_GW

    say("9. BPM + agent: Durable orchestration owns the money; agent does extract/classify/draft")
    text = "ACME Metals\nInvoice No: INV-7781\nVendor ID: V-44\nPO: 4500009001\nTotal due: USD 12,000.00\nTax code: V1"
    s, r = await _json(
        "bpm-host",
        "POST",
        "/api/orchestrators/vendor_invoice",
        json={"invoice_text": text, "tenant": "contoso"},
    )
    iid = r["id"]
    _, st = await _json("bpm-host", "GET", f"/runtime/webhooks/durabletask/instances/{iid}")
    cs = st["customStatus"]
    expect(cs.get("stage") == "awaiting_approval", "waits for human")
    ok(
        "INV-7781 started",
        f"instance {iid[:8]}..., stage={cs['stage']}, parked {cs['invoice_document']}, approver={cs.get('approver')}",
    )
    carol = await login("carol", TOOL_GW)
    s, d = await _json(
        "tool-gateway",
        "POST",
        f"/v1/approvals/{cs['approval_id']}/decision",
        json={"approved": True},
        headers={"authorization": f"Bearer {carol}"},
    )
    expect(s == 200, "carol decides")
    ok("carol approves with her own token at the Tool Gateway", d["status"])
    await _json(
        "bpm-host",
        "POST",
        f"/runtime/webhooks/durabletask/instances/{iid}/raiseEvent/ApprovalDecision",
        json={"approved": True, "approval_id": cs["approval_id"], "approver": d["decided_by"]},
    )
    _, st = await _json("bpm-host", "GET", f"/runtime/webhooks/durabletask/instances/{iid}")
    expect(st["runtimeStatus"] == "Completed" and st["output"]["outcome"] == "completed", "completes")
    ok(
        "process completed",
        f"{st['output']['outcome']}; {st['activityExecutions']} activity executions, {st['replays']} replays; note: {st['output'].get('note')}",
    )
    _, pm = await _json("bpm-host", "GET", f"/v1/process-map/{iid}")
    ok("process id -> graph run ids", pm)

    text2 = text.replace("INV-7781", "INV-7790")
    _, r = await _json(
        "bpm-host",
        "POST",
        "/api/orchestrators/vendor_invoice",
        json={"invoice_text": text2, "tenant": "contoso"},
    )
    iid2 = r["id"]
    _, st2 = await _json("bpm-host", "POST", f"/_local/instances/{iid2}/advance", params={"hours": 49})
    expect(st2["output"]["outcome"] == "timed_out_compensated", "timeout compensates")
    ok(
        "INV-7790: nobody approves, virtual clock +49h",
        f"{st2['output']['outcome']} (parked invoice deleted)",
    )
    _, audit = await _json(
        "tool-gateway",
        "GET",
        "/v1/audit",
        params={"business_key": cs["invoice_document"]},
        headers={"authorization": f"Bearer {carol}"},
    )
    rows = audit["records"]
    expect(len(rows) > 0 and audit["chain_ok"], "audit trail present and hash chain intact")
    ok(f"audit: who did what to invoice {cs['invoice_document']} (hash chain ok={audit['chain_ok']})", "")
    for e in rows[:8]:
        print(
            f"         {e['ts']}  actor={e.get('actor')}  subject={str(e.get('subject'))[:24]}  {e.get('operation')}  -> {e.get('result_class')}"
        )
    RESULTS["bpm"] = {
        "hitl": st["output"]["outcome"],
        "timeout": st2["output"]["outcome"],
        "activity_executions": st["activityExecutions"],
        "replays": st["replays"],
    }


async def summary() -> None:
    from aiip.shared import telemetry

    say("10. Integration observability (from this run)")
    for svc in ("tool-gateway", "mcp-gateway", "a2a-gateway", "event-gateway"):
        _, m = await _json(svc, "GET", "/v1/metrics")
        sbs = {k: f"{v['ok']}/{v['total']} ok" for k, v in m.get("success_by_system", {}).items()}
        ok(f"{svc} success by system", sbs)
        ok(f"{svc} identity mix", m.get("identity_mix"))
        RESULTS.setdefault("metrics", {})[svc] = {
            "success_by_system": m.get("success_by_system"),
            "identity_mix": m.get("identity_mix"),
            "p95_ms_by_operation": m.get("p95_ms_by_operation"),
        }
    local = telemetry.LEDGER.summary()
    ok("worker process completions", local["processes"])


async def run(inproc: bool) -> dict[str, Any]:
    await obo_journey()
    await tool_drills()
    await mcp_drills()
    await event_drills()
    await bpm_drills()
    await summary()
    return RESULTS


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--inproc",
        action="store_true",
        help="call services in-process instead of launching the HTTP topology",
    )
    ap.add_argument("--out", default=".demo-logs/demo-results.json")
    a = ap.parse_args(argv)
    import logging

    logging.getLogger("a2a.server.events.event_queue_v2").setLevel(logging.ERROR)
    t0 = time.perf_counter()
    if a.inproc:
        results = asyncio.run(run(True))
    else:
        from aiip.topology import SERVICES, Topology

        with Topology():
            print(
                f"topology up: {len(SERVICES)} processes on 127.0.0.1:{SERVICES[0].port}-{SERVICES[-1].port}"
            )
            results = asyncio.run(run(False))
    results["wall_clock_s"] = round(time.perf_counter() - t0, 1)
    results["mode"] = "inproc" if a.inproc else "http-topology"
    Path(a.out).parent.mkdir(exist_ok=True)
    Path(a.out).write_text(json.dumps(results, indent=2, default=str))
    print(f"\nDemo finished in {results['wall_clock_s']} s ({results['mode']}); results in {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
