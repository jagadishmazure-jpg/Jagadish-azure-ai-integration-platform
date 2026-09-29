# scripts

| File | What it does |
|---|---|
| [`demo.py`](demo.py) | Run the end-to-end demo (`--inproc` for a single process) |
| [`run_eval_gate.py`](run_eval_gate.py) | Golden-set evals + contract checks; writes `evals/scores.json` |
| [`run_safety_evals.py`](run_safety_evals.py) | Runtime-safety gate: attack scenarios contained, no false quarantines; writes `evals/safety-scores.json` |
| [`export_contracts.py`](export_contracts.py) | Regenerate `control-plane/` (`--check` for CI) |
| [`build_dashboards.py`](build_dashboards.py) | Generate workbook + Grafana JSON from KQL (`--check` for CI) |
| [`overlap_check.py`](overlap_check.py) | Originality check: flags any 8-word run shared with given reference texts |
| [`package_functions.sh`](package_functions.sh) | azd prepackage hook (POSIX): copy `src/aiip` into `functions/` |
| [`package_functions.ps1`](package_functions.ps1) | azd prepackage hook (Windows) |
