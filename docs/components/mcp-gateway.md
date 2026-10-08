# MCP Gateway and servers (`src/aiip/mcp/`, `src/aiip/mcp_servers/`)

A fleet manager for Model Context Protocol servers: catalog, discovery, governed calls, approvals for writes and screening of server output.

**Sections:** [1. Purpose](#1-purpose) · [2. Architecture](#2-architecture) · [3. How it works](#3-how-it-works) · [4. Key files](#4-key-files) · [5. Code excerpts](#5-code-excerpts) · [6. Configuration](#6-configuration) · [7. Commands](#7-commands) · [8. Real output](#8-real-output) · [9. Tests and eval gates](#9-tests-and-eval-gates) · [10. Guardrails](#10-guardrails) · [11. Security and governance](#11-security-and-governance) · [12. Observability](#12-observability) · [13. Failure modes](#13-failure-modes) · [14. Mapping to Azure services](#14-mapping-to-azure-services) · [15. Limitations](#15-limitations) · [16. Interview talking points](#16-interview-talking-points) · [17. Adopt this](#17-adopt-this)

## 1. Purpose

MCP makes it easy to hand an agent many tools. The gateway keeps that safe: agents see only the servers their card allows, servers accept only the gateway's token, writes need a human, and anything a server returns is treated as untrusted.

## 2. Architecture

```mermaid
flowchart LR
    A[agent] --> G[MCP Gateway]
    G --> CAT[catalog: owner, mode, allowed agents]
    G --> AU[authorize agent + tool]
    AU --> W{write?}
    W -- yes --> HITL[approval + idempotency]
    W -- no --> CALL
    HITL --> CALL[call server with McpServer.Invoke token]
    CALL --> S1[sap-orders read-only]
    CALL --> S2[servicenow-incidents HITL writes]
    CALL --> S3[sql-warehouse SELECT-only]
    S1 & S2 & S3 --> SC[screen output] --> A
```

## 3. How it works

1. `catalog.py` lists each server's owner, system, mode and allowed agents.
2. `discover` and `servers` filter the catalog by the caller's card.
3. `_authorize` checks the agent and tool; write tools go through the same approval store as the Tool Gateway.
4. The gateway calls the server over streamable HTTP with a token for `api://aiip-mcp-servers` holding `McpServer.Invoke`, which only the gateway has.
5. `sql_warehouse.guard` allows one SELECT statement over allow-listed tables; PII tables are not exposed.
6. Returned text is screened by `shared/untrusted.screen`; flagged fields are withheld. The regex screen always runs and is the only screen offline. With `AIIP_PROMPT_SHIELDS=1` and `AZURE_CONTENT_SAFETY_ENDPOINT` set, the strings it let through also go to Azure AI Content Safety Prompt Shields (`shared/content_safety.py`) and flagged ones are withheld with the pattern `prompt_shields`.

## 4. Key files

| File | What it does |
|---|---|
| `src/aiip/mcp/gateway.py` | gateway service |
| `src/aiip/mcp/catalog.py` | server catalog |
| `src/aiip/mcp_servers/sap_orders.py` | read-only SAP orders |
| `src/aiip/mcp_servers/servicenow_incidents.py` | incidents with HITL create |
| `src/aiip/mcp_servers/sql_warehouse.py` | SELECT-only SQL |
| `src/aiip/shared/untrusted.py` | regex screen, then optional Prompt Shields |
| `src/aiip/shared/content_safety.py` | opt-in Prompt Shields client: keyless, batched, fail closed |
| `control-plane/mcp-catalog.json` | generated catalog |

## 5. Code excerpts

<!-- code: src/aiip/mcp_servers/sql_warehouse.py::guard -->
```python
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
```
<!-- /code -->

<!-- code: src/aiip/shared/untrusted.py::screen -->
```python
def screen(value: Any, path: str = "") -> tuple[Any, list[dict[str, str]]]:
    """Regex screen, then Prompt Shields when it is switched on. Returns (clean value, flags)."""
    clean, flags = _regex_screen(value, path)
    leaves: list[tuple[str, str]] = []
    _string_leaves(clean, path, leaves)
    todo = [(p, s) for p, s in leaves if s.strip() and s != WITHHELD]
    attacked = content_safety.shield([s for _, s in todo])
    if not attacked:
        return clean, flags
    hit = {p for (p, _), bad in zip(todo, attacked, strict=True) if bad}
    flags += [{"path": p or "/", "patterns": "prompt_shields"} for p, _ in todo if p in hit]
    return _withhold(clean, path, hit), flags
```
<!-- /code -->

## 6. Configuration

Server URLs come from `AIIP_<SERVICE>_URL` in HTTP mode; in-process mode mounts them directly. `CALL_TIMEOUT_S` in `mcp/gateway.py` bounds each call.

| Setting | Default | Effect |
|---|---|---|
| `AIIP_PROMPT_SHIELDS` | unset (off) | `1` adds Azure AI Content Safety Prompt Shields after the regex screen, for MCP output, A2A artifacts and supervisor-retrieved text |
| `AZURE_CONTENT_SAFETY_ENDPOINT` | unset | Content Safety resource endpoint; both settings are needed. Auth is `DefaultAzureCredential` (role `Cognitive Services User`); no key is read |

## 7. Commands

```bash
python -m aiip.mcp_servers sql-warehouse
pytest tests/test_29_mcp_gateway.py -q
```

## 8. Real output

<!-- output: python scripts/doc_demo.py demo 7 -->
```text
=== 7. MCP Gateway: catalog, read-only SQL, PII refusal, injection screening, HITL writes
  [ok] catalog: ['sap-orders (read-only, in-process)', 'servicenow-incidents (read-write (HITL), in-process)', 'sql-warehouse (read-only, in-process)']
  [ok] data-agent may use: ['sql-warehouse']
  [ok] data-agent SELECT on delivery_kpis: 2 rows
  [ok] SELECT on customer_pii: HTTP 400 {"code": "validation_error", "message": "table not exposed: customer_pii", "correlation_id": "<id>
  [ok] ServiceNow incident with an injected instruction: 1 field(s) withheld: {'path': '/description', 'patterns': 'override,prompt_exfil,tool_steering'}
  [ok] MCP write without a human approval: HTTP 428 approval_required
```
<!-- /output -->

## 9. Tests and eval gates

<!-- output: python -m pytest --co -q -p no:cacheprovider tests/test_29_mcp_gateway.py | grep '::' -->
```text
tests/test_29_mcp_gateway.py::test_catalog_has_three_servers_and_only_reviewed_ones_write
tests/test_29_mcp_gateway.py::test_discovery_returns_schemas_and_governance
tests/test_29_mcp_gateway.py::test_read_call_returns_structured_untrusted_data
tests/test_29_mcp_gateway.py::test_read_only_server_refuses_writes_even_with_role
tests/test_29_mcp_gateway.py::test_user_tokens_and_unlisted_agents_are_denied
tests/test_29_mcp_gateway.py::test_write_needs_idempotency_key_and_a_human_approval
tests/test_29_mcp_gateway.py::test_prompt_injection_in_server_output_is_withheld_and_flagged
tests/test_29_mcp_gateway.py::test_screen_catches_common_injection_shapes[payload0]
tests/test_29_mcp_gateway.py::test_screen_catches_common_injection_shapes[payload1]
tests/test_29_mcp_gateway.py::test_screen_catches_common_injection_shapes[payload2]
tests/test_29_mcp_gateway.py::test_screen_leaves_ordinary_business_text_alone
tests/test_29_mcp_gateway.py::test_prompt_shields_is_off_by_default_and_needs_flag_and_endpoint
tests/test_29_mcp_gateway.py::test_prompt_shields_request_is_keyless_and_batched
tests/test_29_mcp_gateway.py::test_screen_runs_prompt_shields_after_the_regex_screen
tests/test_29_mcp_gateway.py::test_prompt_shields_outage_fails_closed
tests/test_29_mcp_gateway.py::test_mcp_gateway_withholds_what_prompt_shields_flags
tests/test_29_mcp_gateway.py::test_sql_server_is_read_only_and_allow_listed[DELETE FROM delivery_kpis-only SELECT]
tests/test_29_mcp_gateway.py::test_sql_server_is_read_only_and_allow_listed[SELECT * FROM delivery_kpis; DROP TABLE delivery_kpis-one statement]
tests/test_29_mcp_gateway.py::test_sql_server_is_read_only_and_allow_listed[SELECT * FROM customer_pii-not exposed]
tests/test_29_mcp_gateway.py::test_sql_server_is_read_only_and_allow_listed[SELECT * FROM delivery_kpis -- comment-forbidden]
tests/test_29_mcp_gateway.py::test_mcp_servers_reject_callers_other_than_the_gateway
```
<!-- /output -->

## 10. Guardrails

- Read-only by default; writes need approval and an idempotency key.
- SQL is restricted to one SELECT over exposed tables.
- Injected instructions in server output are withheld, with the path and pattern recorded.
- **Which screen runs offline:** only the regex screen. Prompt Shields runs only when switched on, never sees text the regex screen already withheld, and fails closed: if the service errors, every string in that batch is withheld.

## 11. Security and governance

- Servers trust only the gateway's role; an agent token is refused.
- Every server has a named owner in the catalog.

## 12. Observability

Spans per call with server, tool and result class; demo step 10 shows MCP success by system.

## 13. Failure modes

| Failure | Result |
|---|---|
| table not exposed | 400 `validation_error` |
| write without approval | 428 `approval_required` |
| injected text | field withheld |
| Prompt Shields unreachable (flag on) | strings in the failed batch withheld and flagged `prompt_shields` |
| server down | 503 with breaker |

## 14. Mapping to Azure services

| Piece | Azure service |
|---|---|
| servers and gateway | Azure Container Apps |
| server identity | Entra app role `McpServer.Invoke` |
| warehouse | Azure Databricks SQL (stand-in here) |
| injection screen | Azure AI Content Safety Prompt Shields (opt-in adapter, unit-tested with a fake transport, not run against Azure) |

## 15. Limitations

- Three servers, all on sandbox stand-ins.
- SQL guard is regex-based; a real deployment should also use a read-only warehouse role.
- The injection regexes miss novel phrasings; the Prompt Shields adapter has not been run against a real Content Safety resource.

## 16. Interview talking points

- Treat MCP server output as untrusted input, like web content.

## 17. Adopt this

1. Add a server under `mcp_servers/` using `_common.py` middleware.
2. Register it in `catalog.py` with owner, mode and allowed agents.
3. Regenerate `control-plane/mcp-catalog.json`.
