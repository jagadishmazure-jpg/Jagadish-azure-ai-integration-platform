# Contributing

This is a personal portfolio, but issues and pull requests are welcome. The bar for a change is the same one CI enforces.

## Set up

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
```

## Checks to run before a pull request

```bash
ruff check . && ruff format --check .
pytest -q                                    # includes the repo hygiene checks
python scripts/run_eval_gate.py --no-write   # eval + contract gate
python scripts/run_safety_evals.py --no-write
python scripts/export_contracts.py --check
python scripts/build_dashboards.py --check
python scripts/demo.py                       # end-to-end over real HTTP
```

Infrastructure (offline, no Azure login needed):

```bash
terraform -chdir=<stack> init -backend=false && terraform -chdir=<stack> validate && terraform -chdir=<stack> test
bicep build <file>.bicep --stdout > /dev/null
```

## Conventions

- Keep everything runnable offline: new code needs a mock or stand-in and a test that uses it.
- Use only synthetic data. Never commit real customer data, personal email addresses, keys, connection strings or `.tfstate` files.
- Every folder has a `README.md` with a `| File | What it does |` table; add a row when you add a file.
- Commit messages follow Conventional Commits (`feat:`, `fix:`, `docs:`, `ci:`, `build:`, `test:`), with a scope where it helps (`feat(infra): ...`).
- Infrastructure changes go into both Bicep and Terraform, or the README says why they differ.
- Docs describe what the code does today. Mark anything else as planned. Numbers (test counts, scores, latencies) must come from a real run.
- Record significant design choices as an ADR in [`docs/adr/`](docs/adr/README.md) and add a line to [`CHANGELOG.md`](CHANGELOG.md).
- The hygiene test also requires the `| File | What it does |` table in every folder README, so CI fails if one is missing.

## Pull request checklist

- [ ] CI is green (`ci`, `infra`; `deploy` only reports its gate)
- [ ] Tests added or updated, and counts in READMEs updated if they changed
- [ ] Folder README tables and links updated
- [ ] No secrets, personal data or generated state files
- [ ] `CHANGELOG.md` entry under **Unreleased**

Security issues: see [`SECURITY.md`](SECURITY.md), not the issue tracker.
