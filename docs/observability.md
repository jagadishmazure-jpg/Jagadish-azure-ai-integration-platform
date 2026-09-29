# Integration observability

APM tells you an HTTP call returned 200. Integration observability asks whether the **business
step happened**: did the order simulate, did the case open, did the invoice post.

## Span attributes

Every gateway hop is an OpenTelemetry span with the usual HTTP/trace fields plus:

| Attribute | Example |
|---|---|
| `integration.system` | `sap`, `salesforce`, `mcp:sql-warehouse`, `a2a:erp-agent` |
| `integration.operation` | `erp.post_parked_invoice` |
| `integration.business_key` | `5105600002`, `ACC-1001` |
| `integration.result_class` | `ok`, `timeout`, `business_reject`, `authz_deny`, `validation_error`, `unavailable`, … |
| `integration.identity_mode` | `obo` or `agent` |
| `integration.actor` / `integration.subject` / `tenant.id` | who called, for whom, which tenant |
| `integration.vendor_code` | vendor error code, e.g. SAP `M8/108` |

**HTTP 200 with an error payload is a failure.** The SAP stand-in answers `200` with a BAPI
`RETURN` of type `E` for an undefined tax code. The connector maps it to `business_reject`, the
span status is `ERROR`, and the gateway answers `422` with a sanitized message.

## Metrics

* `integration.calls` counter and `integration.duration` histogram by system/operation/result class
* `process.completions` by process/event class and outcome (`completed`, `blocked_credit_hold`,
  `timed_out_compensated`, `dead_lettered`, …)
* `integration.identity_mode` counter (OBO vs agent identity)
* queue depths including dead-letter and parked (Event Gateway `/v1/metrics`)

## Dashboards

`observability/queries.kql` is the single source. `scripts/build_dashboards.py` generates both
`azure-monitor-workbook.json` (Application Insights workbook) and `grafana-dashboard.json`
(Azure Monitor data source), and CI fails if the generated files drift.

Panels: success by system · result classes over time · p95 latency per tool · poison-queue depth
· Service Bus dead letters · OBO vs agent identity mix · business rejects hidden behind HTTP 200
· process completions · who did what to a business key.

## Audit

Separate from telemetry: an append-only, **hash-chained** audit log per gateway with actor,
subject, operation, side effect, business key, result and trace id. Arguments are stored as a
digest. `GET /v1/audit?business_key=…` answers "who posted invoice 5105600002" and verifies the
chain.
