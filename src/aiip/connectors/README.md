# connectors

One connector pack per SaaS. See [docs/connectors.md](../../../docs/connectors.md).

| File | What it does |
|---|---|
| [`base.py`](base.py) | Pack contract, error taxonomy, rate-limit hints, vendor HTTP client |
| [`auth.py`](auth.py) | Auth adapters per vendor dialect; Key Vault references only; token-endpoint failure classification |
| [`canonical.py`](canonical.py) | Canonical models (Pydantic, `extra=forbid`): the data-minimization boundary |
| [`registry.py`](registry.py) | `<pack>.<operation>` → pack instance |
| [`salesforce.py`](salesforce.py) | Salesforce: JWT bearer, external-id upsert, error arrays, `Sforce-Limit-Info` |
| [`servicenow.py`](servicenow.py) | ServiceNow: client credentials, correlation-id idempotency, rate-limit headers |
| [`workday.py`](workday.py) | Workday (read-only): ISU refresh token; sensitive HR fields never mapped |
| [`dataverse.py`](dataverse.py) | Dynamics 365 / Dataverse: OBO, alternate-key upsert, burst headers |
| [`sap_odata.py`](sap_odata.py) | SAP S/4HANA OData: CSRF fetch, `Repeatability-Request-ID`, BAPI RETURN → business_reject |
| [`jira.py`](jira.py) | Jira Cloud: API token, label-based idempotency, JQL before create |
| [`__init__.py`](__init__.py) | Package marker |
