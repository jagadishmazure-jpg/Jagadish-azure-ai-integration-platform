"""Policy prover. Declarative rules (policy.yaml) are evaluated before every action the
supervisor takes for an agent: tool calls, sandboxed code, data access, model calls and outbound
messages. Anything not explicitly allowed is denied.

Evaluation order, per action kind:
  1. deny rules (for that kind or "*"): the first one whose conditions all hold wins;
  2. allow rules: the first one whose conditions all hold wins;
  3. otherwise the built-in `default-deny`.

Every decision comes back as a Proof: allow/deny, the rule id that decided it, the exact inputs
that were checked (derived facts such as `on_agent_card` or `payload_classes`, never raw payloads),
the digest of the policy text, and a proof hash. The supervisor writes the proof hash into the
signed audit chain, so a decision can be re-derived later from the same policy and inputs.

Condition forms (a rule's `when` maps an input name to one of these):
  scalar            equals
  [a, b]            value is one of
  {in: [...]} {not_in: [...]} {glob: "kb://*"} {max: n} {min: n}
  {intersects: [...]} {disjoint: [...]}   for list-valued inputs"""

from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from aiip.shared.audit import digest

DEFAULT_POLICY = Path(__file__).with_name("policy.yaml")
ACTIONS = frozenset({"tool", "sandbox", "data", "model", "outbound"})
OPS = frozenset({"in", "not_in", "glob", "max", "min", "intersects", "disjoint", "equals"})


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class Rule:
    id: str
    action: str
    effect: str
    when: dict[str, Any]
    description: str = ""


@dataclass(frozen=True)
class Proof:
    decision: str  # "allow" | "deny"
    rule_id: str
    action: str
    inputs: dict[str, Any]
    policy_version: str
    policy_digest: str
    proof_hash: str
    audit_hash: str | None = None

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "rule_id": self.rule_id,
            "action": self.action,
            "inputs": self.inputs,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "proof_hash": self.proof_hash,
            "audit_hash": self.audit_hash,
        }


def _holds(cond: Any, value: Any) -> bool:
    if value is None:
        return False  # a missing fact never satisfies a condition (fail closed for allow rules)
    if isinstance(cond, list):
        return value in cond
    if not isinstance(cond, dict):
        return value == cond
    vals = set(value) if isinstance(value, list | tuple | set) else {value}
    number = isinstance(value, int | float) and not isinstance(value, bool)
    for op, arg in cond.items():
        if op == "equals":
            ok = value == arg
        elif op == "in":
            ok = value in arg
        elif op == "not_in":
            ok = value not in arg
        elif op == "glob":
            ok = isinstance(value, str) and fnmatch.fnmatchcase(value, arg)
        elif op == "max":
            ok = number and value <= arg
        elif op == "min":
            ok = number and value >= arg
        elif op == "intersects":
            ok = bool(vals & set(arg))
        else:  # disjoint
            ok = not (vals & set(arg))
        if not ok:
            return False
    return True


@dataclass
class PolicyProver:
    rules: list[Rule]
    version: str
    policy_digest: str
    baselines: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_text(cls, text: str) -> PolicyProver:
        doc = yaml.safe_load(text) or {}
        if doc.get("default", "deny") != "deny":
            raise PolicyError("only default: deny is supported")
        rules, seen = [], set()
        for raw in doc.get("rules") or []:
            rid, action, effect = raw.get("id"), raw.get("action"), raw.get("effect", "allow")
            if not rid or rid in seen:
                raise PolicyError(f"rule id missing or duplicated: {rid!r}")
            if action != "*" and action not in ACTIONS:
                raise PolicyError(f"{rid}: unknown action kind {action!r}")
            if effect not in {"allow", "deny"}:
                raise PolicyError(f"{rid}: effect must be allow or deny")
            when = raw.get("when") or {}
            if not when:
                raise PolicyError(f"{rid}: a rule needs at least one condition")
            for k, c in when.items():
                if isinstance(c, dict) and set(c) - OPS:
                    raise PolicyError(f"{rid}.{k}: unknown operator {sorted(set(c) - OPS)}")
            seen.add(rid)
            rules.append(Rule(rid, action, effect, when, raw.get("description", "")))
        return cls(
            rules,
            str(doc.get("version", "0")),
            hashlib.sha256(text.encode()).hexdigest()[:16],
            doc.get("baselines") or {},
        )

    @classmethod
    def load(cls, path: Path | str = DEFAULT_POLICY) -> PolicyProver:
        return cls.from_text(Path(path).read_text(encoding="utf-8"))

    def _proof(self, decision: str, rule_id: str, action: str, inputs: dict[str, Any]) -> Proof:
        body = {
            "decision": decision,
            "rule_id": rule_id,
            "action": action,
            "inputs": inputs,
            "policy_digest": self.policy_digest,
        }
        return Proof(decision, rule_id, action, inputs, self.version, self.policy_digest, digest(body))

    def prove(self, action: str, facts: dict[str, Any]) -> Proof:
        candidates = [r for r in self.rules if r.action in {action, "*"}]
        checked = sorted({k for r in candidates for k in r.when})
        inputs = {k: facts.get(k) for k in checked}
        if action not in ACTIONS:
            return self._proof("deny", "default-deny", action, {k: facts.get(k) for k in sorted(facts)})
        for effect in ("deny", "allow"):
            for r in candidates:
                if r.effect == effect and all(_holds(c, facts.get(k)) for k, c in r.when.items()):
                    return self._proof(effect, r.id, action, {k: facts.get(k) for k in sorted(r.when)})
        return self._proof("deny", "default-deny", action, inputs)


_PROVER: PolicyProver | None = None


def prover() -> PolicyProver:
    global _PROVER
    if _PROVER is None:
        _PROVER = PolicyProver.load()
    return _PROVER
