# bpm

The vendor-invoice process. The orchestration owns money and compliance; agents are activities. See [docs/bpm.md](../../../docs/bpm.md).

| File | What it does |
|---|---|
| [`orchestration.py`](orchestration.py) | Deterministic Durable orchestrator: extract → classify → park → approval/timer → post → pay → draft, with compensation |
| [`activities.py`](activities.py) | Activities; every side effect goes through a gateway as the orchestrator's own identity |
| [`local_runtime.py`](local_runtime.py) | Replay-based Durable Task stand-in with a virtual clock |
| [`host.py`](host.py) | Local host exposing the Durable Functions HTTP API shape and the process→graph map |
| [`__init__.py`](__init__.py) | Package marker |
