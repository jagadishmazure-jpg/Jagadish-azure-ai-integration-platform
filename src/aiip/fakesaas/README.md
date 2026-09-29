# fakesaas: sandbox stand-ins

**These are stand-ins, not the real vendors.** They speak each vendor's wire format closely enough for contract tests and the demo, support fault injection (errors, latency, 200-with-business-error) and tag every response with `x-aiip-sandbox-standin: true`. Fixture data lives in `fixtures/` (no README there, because the loader reads that folder).

| File | What it does |
|---|---|
| [`app.py`](app.py) | Host that mounts every stand-in under one FastAPI app |
| [`state.py`](state.py) | Mutable sandbox state: fixtures, issued tokens, faults, call counters |
| [`sap.py`](sap.py) | SAP OData stand-in (sales orders, deliveries, simulation, purchase orders, parked/posted invoices, BAPI errors) |
| [`salesforce.py`](salesforce.py) | Salesforce REST stand-in with record-level sharing |
| [`servicenow.py`](servicenow.py) | ServiceNow Table API stand-in (includes a ticket with injected instructions) |
| [`workday.py`](workday.py) | Workday stand-in (workers, approvers) |
| [`dataverse.py`](dataverse.py) | Dataverse Web API stand-in enforcing user security roles |
| [`jira.py`](jira.py) | Jira Cloud stand-in |
| [`warehouse.py`](warehouse.py) | Databricks SQL Statement Execution API stand-in over in-memory SQLite |
| [`fixtures/`](fixtures/) | JSON / SQL fixture data per vendor |
| [`__init__.py`](__init__.py) | Package marker |
