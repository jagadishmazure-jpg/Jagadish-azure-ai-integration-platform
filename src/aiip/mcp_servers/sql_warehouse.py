"""MCP server over a Databricks-style SQL warehouse (sandbox stand-in). Read-only by construction:
single SELECT/WITH statement, allow-listed tables only (PII tables are not exposed), row cap, and
the warehouse connection itself is query-only."""

from __future__ import annotations

import re

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from aiip.shared import http
from aiip.shared.secrets import RESOLVER

server = MCPServer(
    "sql-warehouse", instructions="Read-only analytics warehouse (sandbox stand-in). SELECT only."
)
READ = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
TABLES = {
    "delivery_kpis": ["region", "week", "on_time_pct", "late_shipments", "avg_delay_hours"],
    "carrier_performance": ["carrier", "week", "on_time_pct", "claims_open"],
}
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|attach|detach|pragma|grant|revoke|copy|replace|vacuum)\b",
    re.I,
)
PAT_REF = "kv://aiip-local-kv/databricks-sp-token"
MAX_ROWS = 50


class QueryResult(BaseModel):
    columns: list[str] = []
    rows: list[list[str | None]] = []
    row_count: int = 0
    error: str = ""
    message: str = ""


class TableList(BaseModel):
    tables: dict[str, list[str]]


def guard(sql: str) -> str | None:
    """Return a refusal reason, or None when the statement is acceptable."""
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        return "one statement only"
    if not re.match(r"^(select|with)\b", s, re.I):
        return "only SELECT statements are allowed"
    if FORBIDDEN.search(s) or "--" in s or "/*" in s:
        return "statement contains a forbidden keyword or comment"
    referenced = {m.lower() for m in re.findall(r"\b(?:from|join)\s+([A-Za-z_][\w.]*)", s, re.I)}
    if not referenced:
        return "no table referenced"
    if not referenced <= set(TABLES):
        return f"table not exposed: {sorted(referenced - set(TABLES))[0]}"
    return None


@server.tool(annotations=READ)
async def list_tables() -> TableList:
    """Tables and columns this server exposes."""
    return TableList(tables=TABLES)


@server.tool(annotations=READ)
async def run_query(sql: str) -> QueryResult:
    """Run one read-only SELECT against allow-listed tables (max 50 rows)."""
    if reason := guard(sql):
        return QueryResult(error="validation", message=reason)
    async with http.client("fake-saas", timeout=5) as c:
        r = await c.post(
            "/databricks/api/2.0/sql/statements",
            json={"statement": sql.strip().rstrip(";"), "warehouse_id": "aiip-wh", "row_limit": MAX_ROWS},
            headers={"authorization": f"Bearer {RESOLVER.resolve(PAT_REF)}"},
        )
    if r.status_code != 200:
        return QueryResult(
            error="transient" if r.status_code >= 500 else "authz_deny",
            message=f"warehouse HTTP {r.status_code}",
        )
    body = r.json()
    if body["status"]["state"] != "SUCCEEDED":
        return QueryResult(error="business_reject", message="statement failed")
    cols = [c["name"] for c in body["manifest"]["schema"]["columns"]]
    rows = body["result"]["data_array"]
    return QueryResult(columns=cols, rows=rows, row_count=len(rows))
