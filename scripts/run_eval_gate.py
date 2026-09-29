"""Eval + contract gate. Runs every golden case against the real agents (in-process, offline),
checks the integration contracts, writes evals/scores.json and fails below the threshold.

The agent cards publish these scores (`eval_score` in the contract extension), so a card never
claims a number that this gate did not produce."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("AIIP_MODE", "local")
for k in [k for k in os.environ if k.startswith("AIIP_") and k.endswith("_URL")]:
    os.environ.pop(k)
logging.getLogger("a2a.server.events.event_queue_v2").setLevel(logging.ERROR)

THRESHOLD = 0.9


def get_path(obj: Any, path: str) -> Any:
    for part in path.split("."):
        obj = obj[part] if isinstance(obj, dict) else obj[int(part)]
    return obj


def check(expect: dict, output: dict | None, error: str | None) -> tuple[bool, str]:
    if "error" in expect:
        return error == expect["error"], f"expected error {expect['error']}, got {error or 'success'}"
    if error:
        return False, f"unexpected error {error}"
    try:
        value = get_path(output, expect["path"])
    except (KeyError, IndexError, TypeError):
        return False, f"missing {expect['path']}"
    if "equals" in expect:
        ok = value == expect["equals"] or (isinstance(value, float) and value == float(expect["equals"]))
        return ok, f"{expect['path']}={value!r}"
    if "contains" in expect:
        return expect["contains"] in value, f"{expect['path']}={value!r}"
    if "startswith" in expect:
        return str(value).startswith(expect["startswith"]), f"{expect['path']}={value!r}"
    if "min_len" in expect:
        return len(value) >= expect["min_len"], f"len({expect['path']})={len(value)}"
    return False, "no assertion"


async def token_for(agent: str, user: str | None) -> str:
    from aiip.identity.client import IdentityClient, login
    from aiip.identity.registrations import agent_uri

    if agent == "care-planner":
        return await login(user, agent_uri("care-planner"))
    if agent == "ap-invoice-agent":
        return await IdentityClient("bpm-invoice-orchestrator").agent_token(agent_uri(agent))
    user_tok = await login(user, agent_uri("care-planner"))
    return await IdentityClient("care-planner").obo(user_tok, agent_uri(agent))


async def run_case(agent: str, case: dict) -> dict:
    from aiip.a2a import client as a2a
    from aiip.config import DEMO_USERS, TENANTS
    from aiip.shared.errors import GatewayError

    user = case.get("user")
    tenant = TENANTS[DEMO_USERS[user].tenant] if user else TENANTS["contoso"]
    token = await token_for(agent, user)
    output, error = None, None
    try:
        out = await a2a.send(
            agent,
            case["skill"],
            case["input"],
            token=token,
            tenant_id=tenant,
            version=case.get("version"),
            timeout_s=60,
        )
        output = out["output"]
    except GatewayError as exc:
        error = exc.code
    ok, why = check(case["expect"], output, error)
    return {"id": case["id"], "passed": ok, "detail": why}


def contract_checks() -> list[dict]:
    from aiip.a2a.specs import AGENTS
    from aiip.mcp.catalog import SERVERS
    from aiip.tools.registry import TOOLS, validate_registry

    results = [
        {
            "id": "registry-valid",
            "passed": validate_registry() == [],
            "detail": "tool registry schemas and policies",
        }
    ]
    for a in AGENTS:
        unknown = [t for t in a.tools if t not in TOOLS]
        results.append(
            {
                "id": f"card-tools-{a.id}-{a.version}",
                "passed": not unknown,
                "detail": f"unknown tools {unknown}" if unknown else "all card tools registered",
            }
        )
        bad_mcp = [m for m in a.mcp_servers if m not in SERVERS]
        results.append(
            {
                "id": f"card-mcp-{a.id}-{a.version}",
                "passed": not bad_mcp,
                "detail": f"unknown servers {bad_mcp}" if bad_mcp else "all MCP servers catalogued",
            }
        )
        commits = [t for t in a.tools if TOOLS.get(t) and TOOLS[t].side_effect == "commit"]
        declared = a.side_effect_class
        results.append(
            {
                "id": f"card-side-effect-{a.id}-{a.version}",
                "passed": not commits or declared == "commit",
                "detail": f"declares {declared}; commit tools {commits}",
            }
        )
    return results


async def main_async(write: bool) -> int:
    from aiip.fakesaas import state

    scores: dict[str, Any] = {}
    failures = 0
    for f in sorted((ROOT / "evals" / "golden").glob("*.jsonl")):
        agent = f.stem
        cases = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
        results = []
        for c in cases:
            state.reset()
            results.append(await run_case(agent, c))
        passed = sum(r["passed"] for r in results)
        score = round(passed / len(results), 3)
        scores[agent] = {"score": score, "passed": passed, "total": len(results)}
        for r in results:
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"  {mark} {agent:18s} {r['id']:10s} {r['detail']}")
        if score < THRESHOLD:
            failures += 1
    contracts = contract_checks()
    for r in contracts:
        if not r["passed"]:
            print(f"  FAIL contract {r['id']}: {r['detail']}")
    c_pass = sum(r["passed"] for r in contracts)
    print(f"\ncontract checks: {c_pass}/{len(contracts)}")
    for agent, s in scores.items():
        print(f"{agent:18s} {s['passed']}/{s['total']}  score={s['score']}")
    if write:
        out = {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "threshold": THRESHOLD,
            "agents": scores,
            "contracts": {"passed": c_pass, "total": len(contracts)},
        }
        (ROOT / "evals" / "scores.json").write_text(json.dumps(out, indent=2) + "\n")
        print("wrote evals/scores.json")
    ok = failures == 0 and c_pass == len(contracts)
    print("GATE " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true", help="do not update evals/scores.json")
    return asyncio.run(main_async(not ap.parse_args().no_write))


if __name__ == "__main__":
    sys.exit(main())
