"""Generate the Azure Monitor workbook and the Grafana dashboard from observability/queries.kql,
so both always show the same numbers. `--check` fails if the committed JSON is stale (CI)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OBS = ROOT / "observability"

PANELS = [
    # (query name, title, workbook visualization, grafana panel type)
    ("success_by_system", "Success rate by system", "table", "table"),
    (
        "result_classes_over_time",
        "Result classes over time (ok, business_reject, timeout, authz_deny...)",
        "timechart",
        "timeseries",
    ),
    ("p95_by_tool", "p95 latency by tool (ms)", "barchart", "barchart"),
    ("poison_queue_depth", "Poison / parked queue depth (app gauge)", "timechart", "timeseries"),
    (
        "servicebus_dead_letters",
        "Service Bus dead-lettered messages (platform metric)",
        "timechart",
        "timeseries",
    ),
    ("identity_mix", "OBO vs agent identity mix", "piechart", "piechart"),
    ("business_rejects_http_200", "Business rejects (HTTP 200 with error payload)", "table", "table"),
    ("process_completions", "Process completions by outcome", "barchart", "barchart"),
    ("who_did_what", "Audit view: who did what to which business key", "table", "table"),
]


def load_queries() -> dict[str, str]:
    text = (OBS / "queries.kql").read_text()
    out: dict[str, str] = {}
    for block in re.split(r"^// @name ", text, flags=re.M)[1:]:
        name, _, body = block.partition("\n")
        out[name.strip()] = body.strip()
    return out


def workbook(q: dict[str, str]) -> dict:
    items: list[dict] = [
        {
            "type": 1,
            "name": "intro",
            "content": {
                "json": "## AI integration plane\nGateway spans and metrics from the Tool, MCP, A2A, Event and Identity gateways. A call counts as successful only when `integration.result_class == ok`; an HTTP 200 carrying a business error is `business_reject`."
            },
        }
    ]
    for name, title, viz, _ in PANELS:
        items.append(
            {
                "type": 3,
                "name": name,
                "content": {
                    "version": "KqlItem/1.0",
                    "query": q[name],
                    "size": 0,
                    "title": title,
                    "timeContext": {"durationMs": 86400000},
                    "queryType": 0,
                    "resourceType": "microsoft.operationalinsights/workspaces",
                    "visualization": viz,
                },
            }
        )
    return {
        "version": "Notebook/1.0",
        "items": items,
        "fallbackResourceIds": [],
        "$schema": "https://github.com/Microsoft/Application-Insights-Workbooks/blob/master/schema/workbook.json",
    }


def grafana(q: dict[str, str]) -> dict:
    panels = []
    for i, (name, title, _, ptype) in enumerate(PANELS):
        panels.append(
            {
                "id": i + 1,
                "title": title,
                "type": ptype,
                "datasource": {"type": "grafana-azure-monitor-datasource", "uid": "${datasource}"},
                "gridPos": {"h": 8, "w": 12, "x": (i % 2) * 12, "y": (i // 2) * 8},
                "targets": [
                    {
                        "refId": "A",
                        "queryType": "Azure Log Analytics",
                        "azureLogAnalytics": {
                            "query": q[name],
                            "resources": ["${workspace}"],
                            "resultFormat": "time_series" if ptype == "timeseries" else "table",
                        },
                    }
                ],
            }
        )
    return {
        "title": "AI Integration Plane",
        "uid": "aiip-integration",
        "schemaVersion": 39,
        "tags": ["aiip", "integration", "agents"],
        "time": {"from": "now-24h", "to": "now"},
        "templating": {
            "list": [
                {"name": "datasource", "type": "datasource", "query": "grafana-azure-monitor-datasource"},
                {
                    "name": "workspace",
                    "type": "textbox",
                    "label": "Log Analytics workspace resource id",
                    "query": "",
                },
            ]
        },
        "panels": panels,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    q = load_queries()
    missing = [n for n, *_ in PANELS if n not in q]
    if missing:
        print(f"queries.kql is missing: {missing}")
        return 1
    files = {OBS / "azure-monitor-workbook.json": workbook(q), OBS / "grafana-dashboard.json": grafana(q)}
    stale = []
    for path, data in files.items():
        text = json.dumps(data, indent=2) + "\n"
        if a.check:
            if not path.exists() or path.read_text() != text:
                stale.append(path.name)
        else:
            path.write_text(text)
    if stale:
        print(f"stale dashboards: {stale}; run python scripts/build_dashboards.py")
        return 1
    print("dashboards " + ("up to date" if a.check else "written"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
