# identity

Identity Gateway: the only place tokens are exchanged. See [docs/identity.md](../../../docs/identity.md).

| File | What it does |
|---|---|
| [`gateway.py`](gateway.py) | FastAPI service: `/v1/exchange/obo`, `/v1/exchange/agent`, `/v1/exchange/hybrid`, registrations, exchange log, JWKS + `/local/login` (local mode) |
| [`broker.py`](broker.py) | `LocalBroker` (offline, same rules as Entra) and `MsalBroker` (MSAL OBO / client credentials with federated MI assertion) |
| [`registrations.py`](registrations.py) | One app registration per workload: exposed roles, granted roles, OBO targets |
| [`issuer.py`](issuer.py) | Local RS256 issuer (Entra stand-in); key generated in memory at start-up |
| [`client.py`](client.py) | Token client used by agents, workers and gateways |
| [`__init__.py`](__init__.py) | Package marker |
