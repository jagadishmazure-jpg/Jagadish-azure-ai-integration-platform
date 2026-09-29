"""Runtime boundary: the gateway that opens supervised sessions, and the supervisor that sits
between one agent session and everything it can reach.

`RuntimeGateway` manages session lifecycle: it validates the caller's token with the platform's
normal JWT checks (so the session carries the real actor and, for OBO, the signed-in user), hands
the session the current policy, a sandbox executor and a data scope, and keeps the session list.

`Session` (the supervisor) runs the same sequence for every action kind:

  kill switch -> mirror payload to the egress tap -> prove against policy -> write the decision
  (with proof hash) to the signed audit chain -> execute only if allowed -> screen what came
  back -> write the outcome

Action kinds and where they execute:
  tool      Tool Gateway over HTTP with the session's token (+ x-agent-session header)
  sandbox   the sandbox executor (local subprocess by default)
  data      knowledge-base stand-in; content is screened as untrusted before it is returned
  model     model stand-in (deployment name is a policy input)
  outbound  message channel stand-in

Nothing here inspects telemetry for threats: that is the out-of-band monitor's job, on its own
thread or process. The supervisor only enforces decisions (policy, kill switch)."""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from aiip.a2a.specs import allowed_tools
from aiip.identity.registrations import TOOL_GW
from aiip.safety import stand_ins
from aiip.safety.killswitch import KILL, KillSwitch
from aiip.safety.patterns import classify, injection_markers
from aiip.safety.policy import PolicyProver, Proof, prover
from aiip.safety.sandbox import SANDBOX_TOOLS, Sandbox, SandboxSpec, SubprocessSandbox
from aiip.safety.tap import TAP, EgressTap
from aiip.shared import errors as E
from aiip.shared import http
from aiip.shared.audit import AuditLog, digest
from aiip.shared.auth import Principal, validate_token
from aiip.shared.untrusted import screen
from aiip.tools.registry import TOOLS

TELEMETRY = AuditLog("runtime-supervisor")


@dataclass
class Outcome:
    action: str
    target: str
    allowed: bool
    executed: bool
    result_class: str
    data: Any = None
    proof: Proof | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def _inside(path: str, roots: tuple[str, ...]) -> bool:
    p = os.path.realpath(path)
    return any(p == os.path.realpath(r) or p.startswith(os.path.realpath(r).rstrip("/") + "/") for r in roots)


class Session:
    def __init__(
        self, gw: RuntimeGateway, p: Principal, token: str, session_id: str, data_paths: tuple[str, ...]
    ):
        self.gw, self.p, self.token, self.id, self.data_paths = gw, p, token, session_id, data_paths

    # ------------------------------------------------------------------ common path
    def _facts(self, **extra: Any) -> dict[str, Any]:
        return {
            "actor": self.p.actor,
            "identity_mode": self.p.identity_mode,
            "tenant": self.p.tenant,
            **extra,
        }

    def _record(self, event: str, action: str, target: str, **fields: Any) -> dict[str, Any]:
        return self.gw.telemetry.write(
            event=event, action=action, target=target, session=self.id, **self.p.audit_fields(), **fields
        )

    def _gate(self, action: str, target: str, payload: Any, facts: dict[str, Any]) -> Outcome | None:
        """Kill switch, tap, proof, decision record. Returns an Outcome when the action must stop."""
        q = self.gw.kill.check(tenant=self.p.tenant, actor=self.p.actor, session=self.id)
        if q:
            self._record("outcome", action, target, result_class=E.QUARANTINED, executed=False)
            return Outcome(action, target, False, False, E.QUARANTINED, detail=q.public())
        tap_id = None
        if payload is not None:
            tap_id = self.gw.tap.mirror(
                tenant=self.p.tenant,
                actor=self.p.actor,
                session=self.id,
                action=action,
                target=target,
                payload=payload,
            )
        proof = self.gw.prover.prove(action, facts)
        rec = self._record(
            "decision",
            action,
            target,
            decision=proof.decision,
            rule_id=proof.rule_id,
            proof_hash=proof.proof_hash,
            policy_digest=proof.policy_digest,
            args_digest=digest(payload),
            tap_id=tap_id,
            model=facts.get("model"),
        )
        self._last_proof = Proof(**{**proof.__dict__, "audit_hash": rec["hash"]})
        if not proof.allowed:
            self._record("outcome", action, target, result_class="policy_deny", executed=False)
            return Outcome(action, target, False, False, "policy_deny", proof=self._last_proof)
        return None

    def _done(
        self,
        action: str,
        target: str,
        result_class: str,
        data: Any = None,
        executed: bool = True,
        **fields: Any,
    ) -> Outcome:
        self._record("outcome", action, target, result_class=result_class, executed=executed, **fields)
        return Outcome(action, target, True, executed, result_class, data, self._last_proof, fields)

    # ------------------------------------------------------------------ action kinds
    async def tool(self, name: str, args: dict[str, Any], idempotency_key: str | None = None) -> Outcome:
        t = TOOLS.get(name)
        facts = self._facts(
            tool=name,
            known_tool=t is not None,
            on_agent_card=name in allowed_tools(self.p.actor),
            side_effect=t.side_effect if t else None,
            payload_classes=classify(args),
        )
        if stop := self._gate("tool", name, args, facts):
            return stop
        headers = {"authorization": f"Bearer {self.token}", "x-agent-session": self.id}
        if idempotency_key:
            headers["idempotency-key"] = idempotency_key
        async with http.client("tool-gateway", timeout=30) as c:
            r = await c.post(f"/v1/tools/{name}/invoke", json={"args": args}, headers=headers)
        body = r.json()
        rc = body.get("result_class", E.OK) if r.status_code < 400 else body["error"]["code"]
        return self._done(
            "tool", name, rc, body.get("data"), executed=r.status_code < 400, http_status=r.status_code
        )

    async def sandbox(self, name: str, args: dict[str, Any], spec: SandboxSpec | None = None) -> Outcome:
        spec = spec or SandboxSpec(read_paths=self.data_paths)
        paths = [v for k, v in args.items() if k in {"path", "paths"}]
        flat = [p for v in paths for p in (v if isinstance(v, list) else [v])]
        facts = self._facts(
            tool=name,
            network=spec.allow_network,
            read_paths_scoped=all(_inside(str(p), spec.read_paths) for p in flat),
            payload_classes=classify(args),
        )
        if stop := self._gate("sandbox", name, args, facts):
            return stop
        code = SANDBOX_TOOLS[name]["code"] or str(args.get("code", ""))
        res = await self.gw.sandbox.run(code, args, spec, self.id)
        return self._done(
            "sandbox",
            name,
            res.result_class,
            res.result,
            violations=len(res.violations),
            limit=res.limit,
            executor=self.gw.sandbox.name,
            isolation=",".join(res.isolation),
            duration_ms=round(res.duration_ms, 1),
        )

    async def retrieve(self, source: str) -> Outcome:
        facts = self._facts(
            source=source, classification=stand_ins.classification(source), payload_classes=[]
        )
        if stop := self._gate("data", source, None, facts):
            return stop
        doc = stand_ins.retrieve(source)
        if doc is None:
            return self._done("data", source, E.NOT_FOUND)
        markers = injection_markers(doc["text"])
        clean, flags = screen({"text": doc["text"]})
        return self._done(
            "data",
            source,
            E.OK,
            clean["text"],
            untrusted_flags=len(flags),
            injection_patterns=",".join(markers) or None,
        )

    async def model(self, model: str, prompt: str, max_tokens: int = 512) -> Outcome:
        facts = self._facts(model=model, max_tokens=max_tokens, payload_classes=classify(prompt))
        if stop := self._gate("model", model, {"prompt": prompt}, facts):
            return stop
        return self._done("model", model, E.OK, stand_ins.complete(model, prompt, max_tokens), model=model)

    async def send(self, destination: str, body: str) -> Outcome:
        channel = destination.split("://", 1)[0] if "://" in destination else None
        facts = self._facts(channel=channel, destination=destination, payload_classes=classify(body))
        if stop := self._gate("outbound", destination, {"body": body}, facts):
            return stop
        return self._done("outbound", destination, E.OK, stand_ins.send(destination, body))

    async def act(self, kind: str, target: str, payload: dict[str, Any] | None = None) -> Outcome:
        """Generic entry point. Kinds without a handler are still proven (and so denied by default)."""
        handlers = {
            "tool": lambda: self.tool(target, payload or {}),
            "sandbox": lambda: self.sandbox(target, payload or {}),
            "data": lambda: self.retrieve(target),
            "model": lambda: self.model(target, str((payload or {}).get("prompt", ""))),
            "outbound": lambda: self.send(target, str((payload or {}).get("body", ""))),
        }
        if kind in handlers:
            return await handlers[kind]()
        stop = self._gate(kind, target, payload, self._facts(payload_classes=classify(payload or {})))
        assert stop is not None, "an action kind without a handler can never be allowed"
        return stop


class RuntimeGateway:
    def __init__(
        self,
        policy: PolicyProver | None = None,
        kill: KillSwitch = KILL,
        sandbox: Sandbox | None = None,
        telemetry: AuditLog = TELEMETRY,
        tap: EgressTap = TAP,
    ) -> None:
        self.prover = policy or prover()
        self.kill, self.telemetry, self.tap = kill, telemetry, tap
        self.sandbox: Sandbox = sandbox or SubprocessSandbox()
        self.sessions: dict[str, Session] = {}

    async def open_session(
        self,
        token: str,
        *,
        audience: str = TOOL_GW,
        session_id: str | None = None,
        data_paths: tuple[str, ...] = (),
    ) -> Session:
        p = await validate_token(token, audience)  # same checks as every gateway: sig, aud, iss, tenant
        sid = session_id or f"ses-{secrets.token_hex(6)}"
        s = Session(self, p, token, sid, data_paths)
        self.sessions[sid] = s
        self.telemetry.write(
            event="session.open",
            action="session",
            target=sid,
            session=sid,
            **p.audit_fields(),
            opened_at_ns=time.monotonic_ns(),
            policy_digest=self.prover.policy_digest,
        )
        return s

    def close_session(self, sid: str) -> None:
        s = self.sessions.pop(sid, None)
        if s:
            self.telemetry.write(
                event="session.close", action="session", target=sid, session=sid, **s.p.audit_fields()
            )
