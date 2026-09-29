"""Section 37 - Runtime safety, inline half: agent sandbox, policy prover (default deny + proofs),
supervisor, trusted (signed, hash-chained) telemetry and kill-switch enforcement at the gateways."""

from __future__ import annotations

import json
import os

import httpx
import pytest
from tests.conftest import agent_token, call, obo_token, tool

from aiip.identity.registrations import TOOL_GW
from aiip.safety.killswitch import KILL
from aiip.safety.policy import PolicyError, PolicyProver, prover
from aiip.safety.sandbox import (
    ContainerAppsSessions,
    SandboxSpec,
    SandboxUnavailable,
    SubprocessSandbox,
)
from aiip.safety.supervisor import TELEMETRY, RuntimeGateway
from aiip.shared.audit import AuditLog, Signer, verify_records

SBX = SubprocessSandbox()


# ----------------------------------------------------------------------------- sandbox
async def test_sandbox_runs_code_in_a_separate_process_and_returns_the_result():
    r = await SBX.run(
        "import os\nRESULT = {'sum': sum(ARGS['xs']), 'pid': os.getpid()}", {"xs": [1, 2, 3]}, SandboxSpec()
    )
    assert r.ok and r.result["sum"] == 6 and r.result["pid"] != os.getpid()
    assert {"process", "rlimits", "clean-env", "scoped-fs", "audit-hooks"} <= set(r.isolation)


@pytest.mark.parametrize(
    "code",
    [
        "import socket\nsocket.create_connection(('203.0.113.9', 80), timeout=1)",
        "import urllib.request\nurllib.request.urlopen('http://203.0.113.9/', timeout=1)",
        "import socket\nsocket.socket()",
    ],
)
async def test_sandbox_network_is_denied_by_default(code):
    r = await SBX.run(code, {}, SandboxSpec())
    assert r.result_class == "sandbox_violation"
    assert any("network denied" in v for v in r.violations)


async def test_sandbox_file_access_is_scoped(tmp_path):
    (tmp_path / "in.csv").write_text("a\n1\n")
    spec = SandboxSpec(read_paths=(str(tmp_path),))
    ok = await SBX.run(f"RESULT = open({str(tmp_path / 'in.csv')!r}).read()", {}, spec)
    assert ok.ok and ok.result == "a\n1\n"
    scratch = await SBX.run(
        "open('scratch.txt', 'w').write('x')\nRESULT = open('scratch.txt').read()", {}, spec
    )
    assert scratch.ok and scratch.result == "x"  # the per-call working directory is writable
    for code in (
        "RESULT = open('/etc/passwd').read()",
        f"open({str(tmp_path / 'in.csv')!r}, 'a').write('tamper')",  # read-only scope
        "import os\nRESULT = os.listdir('/home')",
        "import os\nos.remove('/tmp/anything')",
    ):
        r = await SBX.run(code, {}, spec)
        assert r.result_class == "sandbox_violation", code
    assert (tmp_path / "in.csv").read_text() == "a\n1\n"


async def test_sandbox_violation_is_reported_even_if_the_code_swallows_it():
    r = await SBX.run(
        "try:\n    open('/etc/hostname').read()\nexcept PermissionError:\n    pass\nRESULT = 'fine'",
        {},
        SandboxSpec(),
    )
    assert r.result == "fine" and r.result_class == "sandbox_violation"


@pytest.mark.parametrize(
    "code",
    ["import subprocess\nsubprocess.run(['id'])", "import os\nos.system('id')", "import ctypes", "import gc"],
)
async def test_sandbox_denies_process_creation_and_native_code(code):
    r = await SBX.run(code, {}, SandboxSpec())
    assert r.result_class == "sandbox_violation", r


async def test_sandbox_cpu_wall_and_memory_limits():
    cpu = await SBX.run("while True: pass", {}, SandboxSpec(cpu_s=1, wall_s=5))
    assert cpu.limit == "cpu_time" and cpu.duration_ms < 4000
    wall = await SBX.run("import time\ntime.sleep(10)", {}, SandboxSpec(wall_s=0.5))
    assert wall.limit == "wall_time" and wall.duration_ms < 3000
    mem = await SBX.run("x = 'a' * (10 ** 9)", {}, SandboxSpec(memory_mb=128))
    assert mem.limit == "memory"


async def test_sandbox_environment_is_an_allow_list(monkeypatch):
    monkeypatch.setenv("AIIP_SUPER_SECRET", "must-not-leak")
    r = await SBX.run("import os\nRESULT = dict(os.environ)", {}, SandboxSpec(env_allow=("TZ",)))
    assert "AIIP_SUPER_SECRET" not in r.result and "AIIP_LOCAL_VAULT_SEED" not in r.result
    assert set(r.result) <= {"TZ", "HOME", "PATH", "LC_CTYPE"}  # LC_CTYPE: Python's own C-locale coercion


async def test_container_apps_dynamic_sessions_adapter_request_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"properties": {"status": "Success", "stdout": '{"result": 6}\n', "stderr": ""}}
        )

    aca = ContainerAppsSessions(
        "https://pool.example.test/", token_provider=lambda: "tok", transport=httpx.MockTransport(handler)
    )
    r = await aca.run("RESULT = sum(ARGS['xs'])", {"xs": [1, 2, 3]}, SandboxSpec(), session_id="ses-1")
    assert r.ok and r.result == 6
    assert seen["url"].startswith("https://pool.example.test/code/execute?")
    assert "identifier=ses-1" in seen["url"] and "api-version=" in seen["url"]
    assert seen["auth"] == "Bearer tok"
    props = seen["body"]["properties"]
    assert props["codeInputType"] == "inline" and props["executionType"] == "synchronous"
    assert "RESULT = sum(ARGS['xs'])" in props["code"]


async def test_container_apps_adapter_refuses_offline_and_per_call_network():
    with pytest.raises(SandboxUnavailable):
        await ContainerAppsSessions("").run("RESULT = 1", {}, SandboxSpec())
    with pytest.raises(SandboxUnavailable):
        await ContainerAppsSessions("https://pool.example.test").run("RESULT = 1", {}, SandboxSpec())
    with pytest.raises(SandboxUnavailable):
        await ContainerAppsSessions("https://p.example.test", token_provider=lambda: "t").run(
            "RESULT = 1", {}, SandboxSpec(allow_network=True)
        )


# ----------------------------------------------------------------------------- policy prover
TOOL_FACTS = {"known_tool": True, "on_agent_card": True, "side_effect": "read", "payload_classes": []}


@pytest.mark.parametrize(
    "action,facts",
    [
        ("shell", {"cmd": "rm -rf /"}),
        ("browser.open", {"url": "https://example.test"}),
        ("", {}),
        ("tool", {"tool": "made.up", "known_tool": False, "on_agent_card": False, "payload_classes": []}),
        (
            "tool",
            {"known_tool": True, "on_agent_card": False, "side_effect": "commit", "payload_classes": []},
        ),
        ("tool", {}),  # missing facts never satisfy an allow rule
        ("data", {"source": "kb://anything", "classification": None, "payload_classes": []}),
        ("model", {"model": "some-new-model", "max_tokens": 10, "payload_classes": []}),
        ("outbound", {"channel": "smtp", "destination": "smtp://example.test", "payload_classes": []}),
        (
            "sandbox",
            {"tool": "sandbox.python", "network": True, "read_paths_scoped": True, "payload_classes": []},
        ),
    ],
)
def test_unknown_or_uncovered_actions_are_denied_by_default(action, facts):
    proof = prover().prove(action, facts)
    assert proof.decision == "deny" and proof.rule_id == "default-deny"


def test_decisions_carry_a_proof_with_rule_inputs_and_hash():
    p = prover()
    allow = p.prove("tool", {**TOOL_FACTS, "actor": "crm-agent"})
    assert allow.allowed and allow.rule_id == "tool.on-agent-card"
    assert allow.inputs == {"known_tool": True, "on_agent_card": True, "side_effect": "read"}
    assert len(allow.proof_hash) == 16 and allow.policy_digest == p.policy_digest
    assert (
        p.prove("tool", {**TOOL_FACTS, "actor": "crm-agent"}).proof_hash == allow.proof_hash
    )  # deterministic
    deny = p.prove("tool", {**TOOL_FACTS, "payload_classes": ["pii.us_ssn"]})
    assert deny.decision == "deny" and deny.rule_id == "egress.no-sensitive-payloads"  # deny beats allow
    assert deny.inputs == {"payload_classes": ["pii.us_ssn"]}
    assert p.prove(
        "data", {"source": "kb://restricted/x", "classification": "internal", "payload_classes": []}
    ).rule_id == ("data.restricted-collections")


def test_policy_file_is_validated():
    with pytest.raises(PolicyError):
        PolicyProver.from_text("default: allow\nrules: []\n")
    with pytest.raises(PolicyError):
        PolicyProver.from_text("rules:\n  - {id: a, action: teleport, when: {x: 1}}\n")
    with pytest.raises(PolicyError):
        PolicyProver.from_text("rules:\n  - {id: a, action: tool, when: {x: {regex: '.*'}}}\n")
    with pytest.raises(PolicyError):
        PolicyProver.from_text("rules:\n  - {id: a, action: tool, when: {}}\n")
    empty = PolicyProver.from_text("rules: []\n")
    assert empty.prove("tool", TOOL_FACTS).rule_id == "default-deny"


# ----------------------------------------------------------------------------- supervisor
async def test_supervisor_writes_every_decision_with_its_proof_to_the_signed_chain():
    tok = await obo_token("alice", "crm-agent", TOOL_GW)
    s = await RuntimeGateway().open_session(tok)
    assert s.p.is_user and s.p.subject.startswith("alice")  # OBO identity carried into the session
    ok = await s.tool("crm.get_account", {"account_id": "ACC-1001"})
    assert ok.allowed and ok.executed and ok.result_class == "ok" and ok.data["account_number"] == "ACC-1001"
    denied = await s.tool("erp.park_invoice", {"po_id": "1"})
    assert not denied.allowed and not denied.executed and denied.result_class == "policy_deny"
    unknown = await s.act("shell", "/bin/sh", {"cmd": "id"})
    assert unknown.result_class == "policy_deny" and unknown.proof.rule_id == "default-deny"
    decisions = TELEMETRY.query(event="decision")
    assert [d["rule_id"] for d in decisions] == ["tool.on-agent-card", "default-deny", "default-deny"]
    for d, o in zip(decisions, (ok, denied, unknown), strict=True):
        assert d["proof_hash"] == o.proof.proof_hash and d["hash"] == o.proof.audit_hash
        assert d["session"] == s.id and d["subject"] == s.p.subject
    assert TELEMETRY.verify_chain()


async def test_supervisor_screens_retrieved_content_and_keeps_payloads_out_of_the_audit():
    tok = await obo_token("alice", "crm-agent", TOOL_GW)
    s = await RuntimeGateway().open_session(tok)
    doc = await s.retrieve("kb://customer-notes/ACC-1001")
    assert doc.data.startswith("[withheld") and doc.detail["untrusted_flags"] == 1
    secret = "Customer SSN 078-05-1120"
    r = await s.tool(
        "crm.upsert_case", {"account_id": "ACC-1001", "subject": "x", "description": secret}, "k-exfil-001"
    )
    assert r.result_class == "policy_deny" and r.proof.rule_id == "egress.no-sensitive-payloads"
    assert "078-05-1120" not in json.dumps(TELEMETRY.records)


async def test_sandbox_tool_through_the_supervisor(tmp_path):
    (tmp_path / "sales.csv").write_text("region,amount\nEast,10.5\nWest,4.5\n")
    tok = await obo_token("alice", "crm-agent", TOOL_GW)
    s = await RuntimeGateway().open_session(tok, data_paths=(str(tmp_path),))
    ok = await s.sandbox("sandbox.csv_stats", {"path": str(tmp_path / "sales.csv"), "column": "amount"})
    assert ok.result_class == "ok" and ok.data == {"rows": 2, "total": 15.0}
    outside = await s.sandbox("sandbox.csv_stats", {"path": "/etc/passwd"})
    assert outside.result_class == "policy_deny"  # path outside the session's data scope never runs
    net = await s.sandbox("sandbox.python", {"code": "RESULT=1"}, SandboxSpec(allow_network=True))
    assert net.result_class == "policy_deny"


# ----------------------------------------------------------------------------- trusted telemetry
def test_signed_chain_detects_edits_deletions_and_forged_records():
    log = AuditLog("t-telemetry")
    for i in range(5):
        log.write(operation=f"op{i}", actor="crm-agent", result_class="ok")
    pub = log.public_key()
    assert log.verify_chain() and verify_records(log.records, pub)[0]
    assert all(r["sig"] and r["key_id"] for r in log.records)

    edited = [dict(r) for r in log.records]
    edited[2]["result_class"] = "authz_deny"
    assert verify_records(edited, pub) == (False, 2, "record hash mismatch")

    deleted = [dict(r) for i, r in enumerate(log.records) if i != 1]
    assert verify_records(deleted, pub)[:2] == (False, 1)

    # an attacker without the key rewrites a record *and* recomputes every hash after it
    from aiip.shared.audit import record_digest

    forged = [dict(r) for r in log.records]
    forged[3]["result_class"] = "ok-forged"
    for i in range(3, len(forged)):
        forged[i]["prev_hash"] = forged[i - 1]["hash"]
        forged[i]["hash"] = record_digest(forged[i])
    assert verify_records(forged)[0]  # the plain hash chain alone cannot tell...
    assert verify_records(forged, pub) == (False, 3, "bad or missing signature")  # ...the signature can

    other = Signer("kv://aiip-local-kv/some-other-key")
    assert verify_records(log.records, other.public_bytes())[0] is False


def test_gateway_audit_logs_are_signed_too():
    from aiip.tools import gateway as tool_gw

    assert tool_gw.AUDIT.signer is not None


# ----------------------------------------------------------------------------- kill switch at the gateways
async def test_every_gateway_enforces_the_kill_switch():
    alice = await obo_token("alice", "crm-agent", TOOL_GW)
    s, _ = await tool(alice, "crm.get_account", {"account_id": "ACC-1001"})
    assert s == 200
    KILL.quarantine(tenant="contoso", scope="actor", key="crm-agent", detector="test", reason="drill")
    s, body = await tool(alice, "crm.get_account", {"account_id": "ACC-1001"})
    assert s == 423 and body["error"]["code"] == "quarantined"
    from aiip.tools import gateway as tool_gw

    assert tool_gw.AUDIT.records[-1]["result_class"] == "quarantined"
    worker = await agent_token("worker-shipment-events", TOOL_GW)
    s, _ = await tool(worker, "erp.get_delivery", {"delivery_id": "80000001"})
    assert s == 200  # other actors are unaffected

    from aiip.identity.registrations import MCP_GW

    data = await agent_token("data-agent", MCP_GW)
    KILL.quarantine(tenant="contoso", scope="actor", key="data-agent", detector="test", reason="drill")
    s, body = await call(
        "mcp-gateway", "POST", "/v1/servers/sql-warehouse/tools/query/call", data, json={"arguments": {}}
    )
    assert s == 423 and body["error"]["code"] == "quarantined"


async def test_session_scoped_quarantine_only_stops_that_session():
    alice = await obo_token("alice", "crm-agent", TOOL_GW)
    KILL.quarantine(tenant="contoso", scope="session", key="ses-bad", detector="test", reason="drill")
    s, _ = await call(
        "tool-gateway",
        "POST",
        "/v1/tools/crm.get_account/invoke",
        alice,
        {"x-agent-session": "ses-bad"},
        json={"args": {"account_id": "ACC-1001"}},
    )
    assert s == 423
    s, _ = await call(
        "tool-gateway",
        "POST",
        "/v1/tools/crm.get_account/invoke",
        alice,
        {"x-agent-session": "ses-good"},
        json={"args": {"account_id": "ACC-1001"}},
    )
    assert s == 200


def test_kill_switch_file_backend_is_shared_across_instances(tmp_path, monkeypatch):
    from aiip.safety.killswitch import KillSwitch

    monkeypatch.setenv("AIIP_KILLSWITCH_FILE", str(tmp_path / "kill.jsonl"))
    writer, reader = KillSwitch(), KillSwitch()
    assert reader.check(tenant="contoso", actor="crm-agent") is None
    writer.quarantine(tenant="contoso", scope="actor", key="crm-agent", detector="x", reason="y")
    assert reader.check(tenant="contoso", actor="crm-agent").detector == "x"
    assert reader.check(tenant="fabrikam", actor="crm-agent") is None  # tenant-scoped
