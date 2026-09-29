"""Serve one MCP server over streamable HTTP with bearer auth: `python -m aiip.mcp_servers sap-orders`."""

from __future__ import annotations

import importlib
import os
import sys

import uvicorn

from aiip.mcp_servers._common import BearerAuth
from aiip.shared import telemetry

MODULES = {
    "sap-orders": "aiip.mcp_servers.sap_orders",
    "servicenow-incidents": "aiip.mcp_servers.servicenow_incidents",
    "sql-warehouse": "aiip.mcp_servers.sql_warehouse",
}


def build_app(name: str):
    server = importlib.import_module(MODULES[name]).server
    app = server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True, host="0.0.0.0"
    )
    app.add_middleware(BearerAuth)
    return app


def main() -> None:
    name = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MCP_SERVER", "sap-orders")
    telemetry.configure(f"mcp-{name}")
    uvicorn.run(
        build_app(name),
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8080")),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
