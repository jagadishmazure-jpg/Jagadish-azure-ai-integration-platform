"""Vendor-invoice orchestration, written against the Durable Functions Python (v1) orchestration
API: `call_activity`, `create_timer`, `wait_for_external_event`, `task_any`, `set_custom_status`.

The process, not the agent, owns money and compliance:
  register run (process id -> graph run id) -> agent: extract -> agent: classify (3-way match)
  -> park invoice in SAP (commit, idempotent) -> [review] approver lookup + approval request
  -> wait for ApprovalDecision OR 48h timer -> post (HITL-gated commit) -> schedule payment
  -> agent: draft remittance note.
Every failure after a commit runs the compensation stack in reverse (delete parked / reverse posted).

Orchestrator code must be deterministic: no I/O, no clocks, no random. All side effects happen in
activities; time comes from `context.current_utc_datetime`."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

APPROVAL_TIMEOUT = timedelta(hours=48)


def vendor_invoice(context) -> Any:
    inp = context.get_input() or {}
    pid = context.instance_id
    run = yield context.call_activity(
        "register_run",
        {"process_id": pid, "process": "vendor-invoice", "tenant": inp.get("tenant", "contoso")},
    )
    base = {"process_id": pid, "run_id": run["run_id"], "tenant": inp.get("tenant", "contoso")}
    context.set_custom_status({"stage": "extract", "run_id": run["run_id"]})

    ext = yield context.call_activity("agent_extract", {**base, "text": inp.get("invoice_text", "")})
    if not ext.get("ok") or ext["result"]["missing"]:
        return (
            yield from _finish(
                context, base, "rejected_incomplete", {"missing": ext.get("result", {}).get("missing")}
            )
        )
    invoice = ext["result"]["invoice"]
    base["business_key"] = invoice["vendor_invoice_no"]

    cls = yield context.call_activity("agent_classify", {**base, "invoice": invoice})
    route = cls["result"]["route"] if cls.get("ok") else "reject"
    if route == "reject":
        return (
            yield from _finish(
                context, base, "rejected_no_po", {"reason": cls.get("result", {}).get("reason")}
            )
        )

    context.set_custom_status({"stage": "park", "route": route, "run_id": run["run_id"]})
    parked = yield context.call_activity(
        "sap_commit",
        {
            **base,
            "tool": "erp.park_invoice",
            "args": {
                "po_id": invoice["po_id"],
                "supplier_id": invoice["supplier_id"],
                "amount": invoice["amount"],
                "tax_code": invoice["tax_code"],
                "vendor_invoice_no": invoice["vendor_invoice_no"],
            },
            "step": "park",
        },
    )
    if not parked["ok"]:
        return (
            yield from _finish(
                context, base, parked["result_class"], {"message": parked.get("message"), "stage": "park"}
            )
        )
    doc = parked["data"]["invoice_document"]
    compensation = [("erp.delete_parked_invoice", doc)]

    approval_id = None
    if route == "review":
        approver = yield context.call_activity(
            "find_approver",
            {**base, "cost_center": cls["result"]["cost_center"], "amount": float(invoice["amount"])},
        )
        req = yield context.call_activity(
            "request_approval",
            {
                **base,
                "invoice_document": doc,
                "invoice": invoice,
                "reason": cls["result"]["reason"],
                "approver": approver.get("approver"),
            },
        )
        approval_id = req["approval_id"]
        context.set_custom_status(
            {
                "stage": "awaiting_approval",
                "approval_id": approval_id,
                "approver": (approver.get("approver") or {}).get("email"),
                "invoice_document": doc,
                "run_id": run["run_id"],
            }
        )
        timer = context.create_timer(context.current_utc_datetime + APPROVAL_TIMEOUT)
        decision_task = context.wait_for_external_event("ApprovalDecision")
        winner = yield context.task_any([decision_task, timer])
        if winner == decision_task:
            timer.cancel()
            decision = decision_task.result or {}
        else:
            yield from _compensate(context, base, compensation)
            return (yield from _finish(context, base, "timed_out_compensated", {"invoice_document": doc}))
        if not decision.get("approved"):
            yield from _compensate(context, base, compensation)
            return (
                yield from _finish(
                    context,
                    base,
                    "rejected_by_approver",
                    {"invoice_document": doc, "approver": decision.get("approver")},
                )
            )
        approval_id = decision.get("approval_id", approval_id)

    context.set_custom_status({"stage": "post", "invoice_document": doc, "run_id": run["run_id"]})
    posted = yield context.call_activity(
        "sap_commit",
        {
            **base,
            "tool": "erp.post_parked_invoice",
            "args": {"invoice_document": doc, "amount": invoice["amount"]},
            "approval_id": approval_id,
            "step": "post",
        },
    )
    if not posted["ok"]:
        yield from _compensate(context, base, compensation)
        return (
            yield from _finish(
                context, base, f"post_failed_{posted['result_class']}_compensated", {"invoice_document": doc}
            )
        )
    compensation = [("erp.reverse_invoice", doc)]

    paid = yield context.call_activity(
        "sap_commit",
        {**base, "tool": "erp.schedule_payment", "args": {"invoice_document": doc}, "step": "pay"},
    )
    if not paid["ok"]:
        yield from _compensate(context, base, compensation)
        return (
            yield from _finish(
                context,
                base,
                "payment_failed_compensated",
                {"invoice_document": doc, "message": paid.get("message")},
            )
        )

    note = yield context.call_activity(
        "agent_draft", {**base, "kind": "remittance", "facts": {**invoice, "invoice_document": doc}}
    )
    return (
        yield from _finish(
            context,
            base,
            "completed",
            {
                "invoice_document": doc,
                "approved_by": posted.get("approved_by"),
                "note": note.get("result", {}).get("text"),
            },
        )
    )


def _compensate(context, base: dict, stack: list[tuple[str, str]]):
    for tool, doc in reversed(stack):
        context.set_custom_status({"stage": "compensating", "tool": tool, "invoice_document": doc})
        yield context.call_activity(
            "sap_commit", {**base, "tool": tool, "args": {"invoice_document": doc}, "step": "compensate"}
        )


def _finish(context, base: dict, outcome: str, detail: dict):
    yield context.call_activity("record_outcome", {**base, "outcome": outcome})
    context.set_custom_status({"stage": "done", "outcome": outcome})
    return {"process_id": base["process_id"], "run_id": base["run_id"], "outcome": outcome, **detail}


ORCHESTRATORS = {"vendor_invoice": vendor_invoice}
