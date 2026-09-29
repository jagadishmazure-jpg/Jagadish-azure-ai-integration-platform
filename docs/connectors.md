# SaaS connector packs

One pack per SaaS, all behind the Tool Gateway. Product teams call `crm.get_account`; they never
learn Salesforce's JWT bearer flow or Dataverse's rate-limit headers.

Each pack provides the same five things (`src/aiip/connectors/base.py`):

1. **Auth adapter** for the vendor's dialect, credentials only as `kv://` references.
2. **Field mapping** into a small canonical model (`canonical.py`, Pydantic with `extra="forbid"`).
3. **Idempotent writes** using the vendor's native mechanism.
4. **Error taxonomy** mapping vendor errors to one vocabulary.
5. **Rate-limit hints** read from vendor headers and fed back to the gateway's limiter.

| Pack | Auth | Idempotent write | Business reject detection | Rate-limit signal |
|---|---|---|---|---|
| Salesforce | OAuth 2.0 JWT bearer (federated user or per-agent integration user) | upsert on `External_Id__c` | upsert `success:false` | `Sforce-Limit-Info` |
| ServiceNow | OAuth client credentials; caller recorded as `caller_id` | `correlation_id` = idempotency key, query before insert | error body by status | `X-RateLimit-*`, `Retry-After` |
| Workday (read-only) | refresh-token grant for an Integration System User | n/a | n/a | 429 |
| Dynamics 365 / Dataverse | Entra OBO to the environment URL (native user security roles) | PATCH upsert by alternate key | error body | `x-ms-ratelimit-burst-remaining-xrm-requests` |
| SAP S/4HANA OData | client credentials + CSRF token fetch before writes | `Repeatability-Request-ID` | **HTTP 200 with BAPI RETURN type E/A** | 429 |
| Jira Cloud | service account + API token | label `aiip-idem-<key>`, JQL before create | error body | `X-RateLimit-Remaining` |

## Error taxonomy

`auth_expired` (refresh once, then stop) · `authz_deny` · `not_found` · `validation` ·
`business_reject` · `rate_limited` (with `retry_after`) · `conflict` · `transient` · `timeout`.
Token-endpoint failures are classified by status: 5xx/429 from the IdP are `transient` /
`rate_limited` (so the circuit breaker counts them), 400/401 are `auth_expired`.

## Minimal data

The mapping is the data-minimization boundary. Workday's national ID, date of birth,
compensation and home address are never mapped, so they never reach a model, a cache or a log.
Tests assert the canonical models reject unmapped fields.

## Sandbox stand-ins

`src/aiip/fakesaas` implements each vendor's wire format closely enough for contract tests
(OData envelopes, Salesforce error arrays, ServiceNow `result` wrappers, Dataverse OData
annotations). They are **not** the vendors. Point a pack at a real sandbox with
`AIIP_SAAS_<VENDOR>_URL` plus the matching Key Vault secrets; none has been run against a real
vendor tenant yet.
