"""Serve one agent: `python -m aiip.agents crm-agent` (Container Apps: AGENT_ID env)."""

from __future__ import annotations

import os
import sys

import uvicorn

from aiip.shared import telemetry


def main() -> None:
    agent_id = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("AGENT_ID", "crm-agent")
    telemetry.configure(f"agent-{agent_id}")
    from aiip.agents.apps import build

    uvicorn.run(
        build(agent_id),
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8080")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
