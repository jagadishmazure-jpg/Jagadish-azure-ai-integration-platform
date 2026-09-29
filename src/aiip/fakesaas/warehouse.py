"""Databricks SQL Statement Execution API stand-in (`/api/2.0/sql/statements`) over in-memory
SQLite opened with `query_only`. Workspace token auth via a Key Vault reference."""

from __future__ import annotations

import sqlite3
import uuid

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from aiip.fakesaas import state
from aiip.shared.secrets import RESOLVER

router = APIRouter(prefix="/databricks")
PAT_REF = "kv://aiip-local-kv/databricks-sp-token"


class Statement(BaseModel):
    statement: str
    warehouse_id: str
    wait_timeout: str = "10s"
    row_limit: int = 100


@router.post("/api/2.0/sql/statements")
async def execute(body: Statement, authorization: str | None = Header(default=None)):
    await state.apply_fault("databricks")
    if authorization != f"Bearer {RESOLVER.resolve(PAT_REF)}":
        return JSONResponse({"error_code": "UNAUTHENTICATED", "message": "Invalid access token."}, 401)
    sid = uuid.uuid4().hex
    try:
        cur = state.WAREHOUSE.execute(body.statement)  # type: ignore[union-attr]
        rows = cur.fetchmany(body.row_limit)
        cols = [
            {"name": d[0], "type_name": "STRING", "position": i} for i, d in enumerate(cur.description or [])
        ]
    except sqlite3.Error as exc:
        return {
            "statement_id": sid,
            "status": {"state": "FAILED", "error": {"error_code": "BAD_REQUEST", "message": str(exc)}},
        }
    return {
        "statement_id": sid,
        "status": {"state": "SUCCEEDED"},
        "manifest": {
            "format": "JSON_ARRAY",
            "schema": {"column_count": len(cols), "columns": cols},
            "total_row_count": len(rows),
        },
        "result": {
            "row_count": len(rows),
            "data_array": [[None if v is None else str(v) for v in r] for r in rows],
        },
    }
