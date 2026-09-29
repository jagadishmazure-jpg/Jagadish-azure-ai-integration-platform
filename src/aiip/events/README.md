# events

Event-driven agents. See [docs/events.md](../../../docs/events.md).

| File | What it does |
|---|---|
| [`gateway.py`](gateway.py) | Event Gateway: CloudEvents admission (schema, tenant, dedupe, budget), routing, metrics, local `/bus/*` |
| [`canonical.py`](canonical.py) | Canonical event classes with business keys, queues and budgets |
| [`bus.py`](bus.py) | In-memory peek-lock bus, HTTP client for it, and the Azure Service Bus / Event Grid code paths |
| [`worker.py`](worker.py) | Worker loop: pull, run graph as the worker identity, emit completion, settle (complete / abandon / dead-letter) |
| [`graphs.py`](graphs.py) | Graphs per event type (order triage, late-shipment handling) |
| [`__init__.py`](__init__.py) | Package marker |
