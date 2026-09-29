# functions

Azure Functions app (Python v2 programming model) hosting the vendor-invoice Durable orchestration. It imports the same `aiip.bpm` code the tests run; `scripts/package_functions.sh` copies `src/aiip` here at package time.

| File | What it does |
|---|---|
| [`function_app.py`](function_app.py) | `DFApp`: HTTP starter, approval webhook, orchestrator trigger, activity triggers |
| [`host.json`](host.json) | Durable Task hub settings |
| [`requirements.txt`](requirements.txt) | Pinned runtime dependencies |
| [`.funcignore`](.funcignore) | Files excluded from the deployment package |
