# shared

What every gateway shares.

| File | What it does |
|---|---|
| [`auth.py`](auth.py) | Entra JWT validation (RS256/JWKS, audience, issuer per tenant, tenant allow-list); Principal with actor + subject |
| [`secrets.py`](secrets.py) | `kv://` references resolved locally or from Key Vault; guard against raw secrets in config |
| [`telemetry.py`](telemetry.py) | OpenTelemetry setup, `integration_span`, result classes, metrics, Azure Monitor exporter |
| [`audit.py`](audit.py) | Append-only hash-chained audit log, Ed25519-signed per record (trusted telemetry), read-only subscription for the out-of-band monitor |
| [`resilience.py`](resilience.py) | Per-tenant token bucket, short-TTL cache, circuit breaker (injectable clocks) |
| [`approvals.py`](approvals.py) | HITL approvals bound to tool, business key and exact arguments |
| [`errors.py`](errors.py) | Sanitized error model returned to agents |
| [`schema.py`](schema.py) | JSON-schema validation helpers |
| [`untrusted.py`](untrusted.py) | Screening of external text for instruction-like content: regex always (the only screen offline), then Prompt Shields when switched on |
| [`content_safety.py`](content_safety.py) | Opt-in Azure AI Content Safety Prompt Shields client (`AIIP_PROMPT_SHIELDS=1` plus `AZURE_CONTENT_SAFETY_ENDPOINT`): Entra token, five documents per request, fail closed |
| [`tracecontext.py`](tracecontext.py) | W3C traceparent helpers |
| [`http.py`](http.py) | httpx client: real HTTP or in-process ASGI |
| [`__init__.py`](__init__.py) | Package marker |
