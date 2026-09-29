"""Export the control-plane contracts to JSON so they can be reviewed in pull requests:
agent cards (A2A 1.0 + integration contract extension), the tool registry, the MCP catalog and
the Entra app-registration plan. `--check` fails when the committed files are stale (CI)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "control-plane"
for k in [k for k in os.environ if k.startswith("AIIP_") and k.endswith("_URL")]:
    os.environ.pop(k)  # cards must not embed a developer's local topology URLs


def build() -> dict[Path, dict]:
    from aiip.a2a.cards import card_json
    from aiip.a2a.specs import AGENTS
    from aiip.identity.registrations import public_view
    from aiip.mcp.catalog import SERVERS
    from aiip.tools.registry import TOOLS

    files: dict[Path, dict] = {}
    for a in AGENTS:
        if a.kind == "a2a-agent":
            files[OUT / "agent-cards" / f"{a.id}@{a.version}.json"] = card_json(a)
    files[OUT / "tool-registry.json"] = {"tools": [t.public() for t in TOOLS.values()]}
    files[OUT / "mcp-catalog.json"] = {"servers": [s.public() for s in SERVERS.values()]}
    files[OUT / "app-registrations.json"] = {
        "note": "Plan for one Entra app registration per workload. client_id values are local placeholders; the real ones come from `az ad app create` (docs/identity.md).",
        "registrations": public_view(),
    }
    return files


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    stale = []
    for path, data in build().items():
        text = json.dumps(data, indent=2, sort_keys=True) + "\n"
        if a.check:
            if not path.exists() or path.read_text() != text:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    if stale:
        print("stale contract files (run python scripts/export_contracts.py):\n  " + "\n  ".join(stale))
        return 1
    print("contracts " + ("up to date" if a.check else "written"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
