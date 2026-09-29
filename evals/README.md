# evals

Eval and contract gate. `python scripts/run_eval_gate.py` runs the golden cases against each agent and checks contracts (card side-effect class matches its tools, schemas valid). Scores are written back to the agent cards.

| File | What it does |
|---|---|
| [`golden/`](golden/) | Golden cases per agent (JSONL) |
| [`scores.json`](scores.json) | Latest scores (read into the agent cards' eval score) |
