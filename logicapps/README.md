# `logicapps/`: Logic Apps alternative for the vendor-invoice process

The same process as [`functions/`](../functions/README.md), expressed as a Logic Apps **Standard**
stateful workflow. It is here for comparison: some teams already run their approval flows in Logic
Apps and would rather add agent steps there than adopt Durable Functions. It is **reference only**:
it is not deployed by `azd` and has not been run against Azure.

| File | What it does |
|---|---|
| [`vendor-invoice/workflow.json`](vendor-invoice/workflow.json) | Service Bus trigger (`invoice-events`) → A2A call to `ap-invoice-agent` (`SendMessage`, managed-identity auth) → park invoice via the Tool Gateway with an idempotency key derived from the run id → approval request → `HttpWebhook` wait with a 48-hour timeout → post + pay inside a Scope, with `runAfter: Failed/TimedOut` compensation (reverse) → delete-parked compensation on rejection or timeout. |

## How it maps to the Durable version

| Concern | Durable Functions (`aiip.bpm.orchestration`) | Logic Apps (`workflow.json`) |
|---|---|---|
| Parent of money/compliance steps | orchestrator function | the workflow run |
| Agent as a step | `agent_extract` / `agent_classify` / `agent_draft` activities | HTTP actions to the A2A gateway |
| HITL | `wait_for_external_event` + `create_timer`, `task_any` | `HttpWebhook` with `limit.timeout: PT48H` |
| Idempotency | key = process id + step + tool | key = `workflow().run.name` + step |
| Compensation | explicit stack, reversed | `runAfter` on `Failed` / `TimedOut` |
| Process id → graph run id | `register_run` activity | run name carried in headers |

Note: the webhook subscribe URL (`/v1/approvals/{id}/subscribe`) is part of the reference design,
not implemented by the Tool Gateway in this repo (the Durable path wakes on a raised event instead).
