"""Section 27 - The integration plane as a platform: five separate gateway services with shared
Entra auth, Key Vault references and OpenTelemetry; agents hold no system connections or secrets;
the end-to-end demo exercises every drill."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from tests.conftest import call

from aiip.shared.secrets import SecretRef, SecretRefError, assert_refs_only

SRC = Path(__file__).resolve().parents[1] / "src" / "aiip"
GATEWAYS = {
    "identity": "aiip.identity.gateway",
    "tool-gateway": "aiip.tools.gateway",
    "mcp-gateway": "aiip.mcp.gateway",
    "a2a-gateway": "aiip.a2a.gateway",
    "event-gateway": "aiip.events.gateway",
}


@pytest.mark.parametrize("service,module", sorted(GATEWAYS.items()))
async def test_each_gateway_is_its_own_fastapi_service(service, module):
    import importlib

    app = importlib.import_module(module).app
    assert isinstance(app, FastAPI)
    s, body = await call(service, "GET", "/healthz")
    assert s == 200 and body["ok"] is True


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


@pytest.mark.parametrize(
    "path",
    [
        *sorted((SRC / "agents").glob("*.py")),
        SRC / "events" / "graphs.py",
        SRC / "bpm" / "activities.py",
        SRC / "bpm" / "orchestration.py",
    ],
    ids=lambda p: p.name,
)
def test_agents_and_graphs_never_touch_systems_directly(path):
    forbidden = (
        "aiip.connectors",
        "aiip.fakesaas",
        "aiip.mcp_servers",
        "aiip.shared.secrets",
        "msal",
        "azure.servicebus",
        "azure.keyvault",
        "httpx",
    )
    bad = [m for m in _imports(path) if m.startswith(forbidden)]
    assert not bad, f"{path.name} imports {bad}: go through a gateway instead"


async def test_gateways_share_one_token_validator():
    for module in GATEWAYS.values():
        text = (SRC / (module.removeprefix("aiip.").replace(".", "/") + ".py")).read_text()
        assert "aiip.shared.auth" in text, module
    s, _ = await call("tool-gateway", "GET", "/v1/tools")
    assert s == 401  # no token, no catalog


def test_secret_references_only():
    assert_refs_only({"salesforce": {"key_ref": "kv://aiip-local-kv/salesforce-connected-app-key"}})
    with pytest.raises(SecretRefError):
        assert_refs_only({"sap": {"client_secret": "hunter2"}})
    with pytest.raises(SecretRefError):
        SecretRef.parse("https://vault/secret")


def test_every_hard_coded_credential_in_src_is_a_kv_reference():
    import re

    for f in SRC.rglob("*.py"):
        for m in re.finditer(r"(?i)(secret|password|token|api_key)[A-Z_]*\s*=\s*\"([^\"]+)\"", f.read_text()):
            assert m.group(2).startswith("kv://") or m.group(2) in {"", "Bearer"} or " " in m.group(2), (
                f"{f.name}: {m.group(0)}"
            )


async def test_spans_are_emitted_through_opentelemetry():
    from aiip.shared import telemetry

    await call("identity", "GET", "/healthz")
    with telemetry.integration_span("probe", service="t", system="x", operation="y"):
        pass
    assert any(s.name == "probe" for s in telemetry.finished_spans())


def test_end_to_end_demo_runs_every_drill_in_process(tmp_path):
    from aiip import demo

    demo.RESULTS.clear()
    assert demo.main(["--inproc", "--out", str(tmp_path / "r.json")]) == 0
    r = json.loads((tmp_path / "r.json").read_text())
    assert (
        r["idempotency"]["replay_sources"] == ["gateway", "vendor"]
        and r["idempotency"]["sap_invoices_for_key"] == 1
    )
    assert r["business_reject"]["code"] == "business_reject"
    assert "open" in str(r["breaker"]["transitions"])
    assert r["bpm"] == {**r["bpm"], "hitl": "completed", "timeout": "timed_out_compensated"}
    assert r["events"]["storm"]["parked_budget"] >= 1
    assert r["mcp"]["injection_flags"] == 1
