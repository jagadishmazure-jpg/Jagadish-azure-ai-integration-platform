# evals

Eval and contract gate. `python scripts/run_eval_gate.py` runs the golden cases against each agent and checks contracts (card side-effect class matches its tools, schemas valid). Scores are written back to the agent cards.

| File | What it does |
|---|---|
| [`golden/`](golden/) | Golden cases per agent (JSONL) |
| [`scores.json`](scores.json) | Latest scores (read into the agent cards' eval score) |
| [`safety-scenarios.yaml`](safety-scenarios.yaml) | Runtime-safety suite: attack scenarios (injection, exfiltration, loop, disallowed tool, drift, sandbox escape, probing) and benign runs |
| [`safety-scores.json`](safety-scores.json) | Latest safety gate result: containment rate, false quarantines, measured containment latency |
