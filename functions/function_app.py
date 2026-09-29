"""Azure Functions (Python v2 programming model) app hosting the vendor-invoice process on Durable
Functions. The orchestrator and activities are the same code the local runtime executes
(`aiip.bpm.orchestration`, `aiip.bpm.activities`); this file only wires triggers.

Deploy target: Flex Consumption, Python 3.13, Durable storage = the function app's storage account
(identity-based connection). Activities call the A2A and Tool gateways with the function app's
managed identity, federated to the `bpm-invoice-orchestrator` app registration."""

from __future__ import annotations

import azure.durable_functions as df
import azure.functions as func

from aiip.bpm import activities
from aiip.bpm.orchestration import vendor_invoice as _vendor_invoice

app = df.DFApp(http_auth_level=func.AuthLevel.FUNCTION)


@app.route(route="orchestrators/{name}", methods=["POST"])
@app.durable_client_input(client_name="client")
async def http_start(req: func.HttpRequest, client) -> func.HttpResponse:
    """HTTP starter (fronted by APIM). Body = orchestration input."""
    instance_id = await client.start_new(req.route_params["name"], client_input=req.get_json())
    return client.create_check_status_response(req, instance_id)


@app.route(route="approvals/{instance_id}", methods=["POST"])
@app.durable_client_input(client_name="client")
async def approval_callback(req: func.HttpRequest, client) -> func.HttpResponse:
    """Wake-up from the human work queue. The approval itself is verified by the Tool Gateway."""
    body = req.get_json()
    await client.raise_event(req.route_params["instance_id"], "ApprovalDecision", body)
    return func.HttpResponse(status_code=202)


@app.orchestration_trigger(context_name="context")
def vendor_invoice(context: df.DurableOrchestrationContext):
    result = yield from _vendor_invoice(context)
    return result


@app.activity_trigger(input_name="payload")
async def register_run(payload: dict) -> dict:
    return await activities.register_run(payload)


@app.activity_trigger(input_name="payload")
async def agent_extract(payload: dict) -> dict:
    return await activities.agent_extract(payload)


@app.activity_trigger(input_name="payload")
async def agent_classify(payload: dict) -> dict:
    return await activities.agent_classify(payload)


@app.activity_trigger(input_name="payload")
async def agent_draft(payload: dict) -> dict:
    return await activities.agent_draft(payload)


@app.activity_trigger(input_name="payload")
async def sap_commit(payload: dict) -> dict:
    return await activities.sap_commit(payload)


@app.activity_trigger(input_name="payload")
async def find_approver(payload: dict) -> dict:
    return await activities.find_approver(payload)


@app.activity_trigger(input_name="payload")
async def request_approval(payload: dict) -> dict:
    return await activities.request_approval(payload)


@app.activity_trigger(input_name="payload")
async def record_outcome(payload: dict) -> dict:
    return await activities.record_outcome(payload)
