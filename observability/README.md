# observability

Dashboards generated from one KQL source by `scripts/build_dashboards.py` (CI checks they are current). See [docs/observability.md](../docs/observability.md).

| File | What it does |
|---|---|
| [`queries.kql`](queries.kql) | Named KQL queries (success by system, p95 per tool, poison queue, identity mix, business rejects, completions, who-did-what) |
| [`azure-monitor-workbook.json`](azure-monitor-workbook.json) | Application Insights workbook |
| [`grafana-dashboard.json`](grafana-dashboard.json) | Grafana dashboard (Azure Monitor data source) |
