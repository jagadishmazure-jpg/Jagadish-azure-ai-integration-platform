# tools

Tool Gateway: the single policy chokepoint for function calls.

| File | What it does |
|---|---|
| [`gateway.py`](gateway.py) | Invocation pipeline: auth → card allow-list → identity policy → schema → HITL → idempotency → rate limit / cache / breaker / timeout → connector → output schema → audit |
| [`registry.py`](registry.py) | Tool definitions: system, side-effect class, identity policy, app role, business key, TTL, timeout, HITL threshold, schemas |
| [`__init__.py`](__init__.py) | Package marker |
