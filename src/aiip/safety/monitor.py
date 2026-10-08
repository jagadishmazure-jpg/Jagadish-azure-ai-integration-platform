"""Out-of-band monitor. Reads the signed audit streams (gateways + supervisor) and the egress
tap, and never sits in an agent's call path: writers only flip a wake-up flag, and every check
below runs on the monitor's own thread (or in its own process, see `main`).

On ingest every record is verified (chain link, record hash, Ed25519 signature). Then detectors:

  prompt_injection    instruction-like markers flagged in retrieved / MCP / A2A content
  data_exfiltration   secret or regulated-PII patterns in any mirrored outbound payload
  tool_loop           the same action + arguments repeated N times in a window
  rate_spike          more than M actions per second from one session
  deny_burst          K policy / authorization denials in a window
  drift               a tool outside the agent's baseline (card) or a model outside its baseline
  sandbox_violation   the sandbox refused a network, file, process or native-code attempt
  telemetry_tamper    a record that fails verification

A detection quarantines the session (or the actor when no session is known) through the kill
switch that the gateways and supervisor enforce, and is itself written to a signed alert log
with the measured containment latency: kill-switch time minus the triggering record's
`t_mono_ns` (both from the system-wide monotonic clock, so they are comparable across processes)."""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from typing import Any

from aiip.a2a.specs import allowed_mcp_servers, allowed_tools
from aiip.safety.killswitch import KILL, KillSwitch
from aiip.safety.patterns import classify
from aiip.safety.policy import prover
from aiip.safety.sandbox import SANDBOX_TOOLS
from aiip.safety.tap import EgressTap
from aiip.shared.audit import GENESIS, AuditLog, record_digest, verify_signature

GATEWAY_ACTION = {"tool": "tool", "mcp": "tool", "a2a": "a2a", "event": "event"}
DENY_CLASSES = {"policy_deny", "authz_deny"}


@dataclass(frozen=True)
class MonitorConfig:
    loop_repeats: int = 8
    loop_window_s: float = 10.0
    rate_max: int = 40
    rate_window_s: float = 1.0
    deny_burst: int = 3
    deny_window_s: float = 10.0
    poll_s: float = 0.005


@dataclass
class Detection:
    detector: str
    tenant: str
    scope: str
    key: str
    reason: str
    evidence: str
    source: str
    event_ns: int
    contained_ns: int

    @property
    def latency_ms(self) -> float:
        return round((self.contained_ns - self.event_ns) / 1e6, 3)


# ----------------------------------------------------------------------------- sources
class LogSource:
    """In-process reader over an AuditLog (read-only copies, cursor-based)."""

    def __init__(self, log: AuditLog) -> None:
        self.log, self.name, self.cursor = log, log.name, 0
        self.public_key = log.public_key()
        self.last_hash = GENESIS

    def read(self) -> list[dict[str, Any]]:
        if len(self.log.records) < self.cursor:  # log was reset
            self.cursor, self.last_hash = 0, GENESIS
        recs = self.log.read_from(self.cursor)
        self.cursor += len(recs)
        return recs


class JsonlDirSource:
    """Tails `AIIP_AUDIT_DIR`-style JSONL files written by other processes."""

    def __init__(self, directory: str, public_key: bytes | None) -> None:
        self.dir, self.public_key = directory, public_key
        self.name = f"jsonl:{directory}"
        self.offsets: dict[str, int] = {}
        self.last: dict[str, str] = {}

    def read(self) -> list[dict[str, Any]]:
        out = []
        for path in sorted(glob.glob(os.path.join(self.dir, "*.jsonl"))):
            with open(path, encoding="utf-8") as f:
                f.seek(self.offsets.get(path, 0))
                chunk = f.read()
            end = chunk.rfind("\n") + 1
            self.offsets[path] = self.offsets.get(path, 0) + len(chunk[:end].encode())
            out += [json.loads(line) for line in chunk[:end].splitlines() if line.strip()]
        return out


# ----------------------------------------------------------------------------- monitor
class OutOfBandMonitor:
    def __init__(
        self,
        sources: list[LogSource | JsonlDirSource],
        tap: EgressTap | None = None,
        kill: KillSwitch = KILL,
        config: MonitorConfig | None = None,
        alerts: AuditLog | None = None,
    ) -> None:
        self.sources, self.tap, self.kill = sources, tap, kill
        self.config = config or MonitorConfig()
        self.alerts = alerts if alerts is not None else AuditLog("runtime-monitor")
        self.baselines = prover().baselines
        self.detections: list[Detection] = []
        self.processed = 0
        self.tamper_events = 0
        self._tap_seq = 0
        self._chain: dict[str, str] = {}
        self._windows: dict[tuple, deque[float]] = defaultdict(deque)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._drain_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # lifecycle ----------------------------------------------------------------
    def start(self) -> OutOfBandMonitor:
        for s in self.sources:
            if isinstance(s, LogSource):
                s.log.subscribe(self._wake.set)
        if self.tap is not None:
            self.tap.subscribe(self._wake.set)
        self._thread = threading.Thread(target=self._run, name="oob-monitor", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2)
        for s in self.sources:
            if isinstance(s, LogSource):
                s.log.unsubscribe(self._wake.set)
        if self.tap is not None:
            self.tap.unsubscribe(self._wake.set)
        self.drain()

    def __enter__(self) -> OutOfBandMonitor:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.config.poll_s)
            self._wake.clear()
            self.drain()

    def drain(self) -> int:
        with self._drain_lock:
            n = 0
            for src in self.sources:
                for rec in src.read():
                    n += 1
                    self._ingest(src, rec)
            if self.tap is not None:
                for entry in self.tap.read_after(self._tap_seq):
                    self._tap_seq = entry["seq"]
                    n += 1
                    self._inspect_egress(entry)
            self.processed += n
            return n

    # verification -------------------------------------------------------------
    def _verified(self, src, rec: dict[str, Any]) -> bool:
        chain_key = f"{src.name}|{rec.get('log')}"
        prev = self._chain.get(chain_key, GENESIS)
        if rec.get("prev_hash") == GENESIS and prev != GENESIS and isinstance(src, LogSource):
            prev = GENESIS  # the in-process log was reset
        ok = (
            rec.get("prev_hash") == prev
            and rec.get("hash") == record_digest(rec)
            and (src.public_key is None or verify_signature(src.public_key, rec["hash"], rec.get("sig", "")))
        )
        if ok:
            self._chain[chain_key] = rec["hash"]
        return ok

    def _ingest(self, src, rec: dict[str, Any]) -> None:
        if not self._verified(src, rec):
            self.tamper_events += 1
            self._contain(
                rec, "telemetry_tamper", "record failed chain/signature verification", scope="actor"
            )
            return
        self._detect(rec)

    # detectors ----------------------------------------------------------------
    def _principal(self, rec: dict[str, Any]) -> tuple[str, str, str]:
        tenant = rec.get("tenant") or "unknown"
        if rec.get("session"):
            return tenant, "session", rec["session"]
        return tenant, "actor", rec.get("actor") or "unknown"

    def _count(self, key: tuple, window_s: float, now_s: float) -> int:
        q = self._windows[key]
        q.append(now_s)
        while q and now_s - q[0] > window_s:
            q.popleft()
        return len(q)

    def _baseline_models(self, actor: str) -> list[str]:
        b = self.baselines.get(actor) or self.baselines.get("default") or {}
        return list(b.get("models") or [])

    def _detect(self, rec: dict[str, Any]) -> None:
        cfg = self.config
        log = rec.get("log", "")
        event = rec.get("event") or ("gateway" if rec.get("gateway") else None)
        if event is None or rec.get("result_class") == "quarantined":
            return None
        action = rec.get("action") or GATEWAY_ACTION.get(rec.get("gateway", ""), "")
        target = rec.get("target") or rec.get("operation") or ""
        actor = rec.get("actor") or ""
        who = self._principal(rec)
        now_s = rec.get("t_mono_ns", time.monotonic_ns()) / 1e9

        flags = int(rec.get("untrusted_flags") or rec.get("flags") or 0)
        if flags or rec.get("injection_patterns"):
            return self._contain(rec, "prompt_injection", f"instruction-like content in {target}")
        if rec.get("result_class") == "sandbox_violation":
            return self._contain(rec, "sandbox_violation", f"{target} attempted to leave its sandbox")

        if event in {"decision", "gateway"} and action:
            if action == "tool":
                if rec.get("gateway") == "mcp":
                    server = target.split(".", 1)[0]
                    if server not in allowed_mcp_servers(actor):
                        return self._contain(rec, "drift", f"unexpected MCP server {server} for {actor}")
                elif target not in allowed_tools(actor) and not target.startswith("approval."):
                    return self._contain(rec, "drift", f"unexpected tool {target} for {actor}")
            if action == "sandbox" and target not in SANDBOX_TOOLS:
                return self._contain(rec, "drift", f"unexpected sandbox tool {target}")
            if action == "model" and rec.get("model") not in self._baseline_models(actor):
                return self._contain(rec, "drift", f"unexpected model {rec.get('model')} for {actor}")

            denied = rec.get("decision") == "deny" or rec.get("result_class") in DENY_CLASSES
            if denied and self._count((log, *who, "deny"), cfg.deny_window_s, now_s) >= cfg.deny_burst:
                return self._contain(
                    rec, "deny_burst", f"{cfg.deny_burst}+ denials within {cfg.deny_window_s:g}s"
                )
            loop_key = (log, *who, "loop", target, rec.get("args_digest"))
            if self._count(loop_key, cfg.loop_window_s, now_s) >= cfg.loop_repeats:
                return self._contain(
                    rec, "tool_loop", f"{target} repeated {cfg.loop_repeats}x with identical args"
                )
            if self._count((log, *who, "rate"), cfg.rate_window_s, now_s) > cfg.rate_max:
                return self._contain(rec, "rate_spike", f"more than {cfg.rate_max} actions/s")
        return None

    def _inspect_egress(self, entry: dict[str, Any]) -> None:
        classes = classify(entry["payload"])
        if classes:
            rec = {
                "tenant": entry["tenant"],
                "actor": entry["actor"],
                "session": entry["session"],
                "t_mono_ns": entry["t_mono_ns"],
                "hash": f"tap:{entry['seq']}",
                "log": "egress-tap",
            }
            self._contain(
                rec, "data_exfiltration", f"{','.join(classes)} in {entry['action']} {entry['target']}"
            )

    # containment --------------------------------------------------------------
    def _contain(self, rec: dict[str, Any], detector: str, reason: str, scope: str | None = None) -> None:
        tenant, sc, key = self._principal(rec)
        if scope == "actor":
            sc, key = "actor", rec.get("actor") or "unknown"
        if self.kill.check(tenant=tenant, **({"session": key} if sc == "session" else {"actor": key})):
            return  # already contained; keep the first detection as the record of why
        q = self.kill.quarantine(
            tenant=tenant,
            scope=sc,
            key=key,
            detector=detector,
            reason=reason,
            evidence=str(rec.get("hash", "")),
        )
        d = Detection(
            detector,
            tenant,
            sc,
            key,
            reason,
            str(rec.get("hash", "")),
            str(rec.get("log", "")),
            int(rec.get("t_mono_ns") or q.at_ns),
            q.at_ns,
        )
        self.detections.append(d)
        self.alerts.write(
            event="quarantine",
            detector=detector,
            tenant=tenant,
            scope=sc,
            key=key,
            reason=reason,
            evidence=d.evidence,
            source=d.source,
            latency_ms=d.latency_ms,
        )


def for_platform(tap: EgressTap | None = None, **kw: Any) -> OutOfBandMonitor:
    """Monitor wired to every in-process gateway log plus the supervisor chain and egress tap."""
    from aiip.a2a import gateway as a2a_gw
    from aiip.mcp import gateway as mcp_gw
    from aiip.safety.supervisor import TELEMETRY
    from aiip.safety.tap import TAP
    from aiip.tools import gateway as tool_gw

    logs = [tool_gw.AUDIT, mcp_gw.AUDIT, a2a_gw.AUDIT, TELEMETRY]
    return OutOfBandMonitor([LogSource(x) for x in logs], tap if tap is not None else TAP, **kw)


def main(argv: list[str] | None = None) -> int:
    """Run the monitor as its own process over JSONL audit files; quarantines go to the kill-switch file."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--audit-dir", required=True)
    ap.add_argument("--killswitch-file", required=True)
    ap.add_argument("--public-key", help="hex Ed25519 public key of the writers")
    ap.add_argument("--ready-file")
    ap.add_argument("--max-seconds", type=float, default=60)
    ap.add_argument("--poll-ms", type=float, default=2)
    a = ap.parse_args(argv)
    os.environ["AIIP_KILLSWITCH_FILE"] = a.killswitch_file
    os.environ.pop("AIIP_AUDIT_DIR", None)  # never tail our own alerts
    kill = KillSwitch()
    src = JsonlDirSource(a.audit_dir, bytes.fromhex(a.public_key) if a.public_key else None)
    mon = OutOfBandMonitor(
        [src],
        None,
        kill,
        MonitorConfig(poll_s=a.poll_ms / 1000),
        alerts=AuditLog("runtime-monitor", signer=None),
    )
    if a.ready_file:
        open(a.ready_file, "w").close()
    deadline = time.monotonic() + a.max_seconds
    while time.monotonic() < deadline:
        mon.drain()
        time.sleep(a.poll_ms / 1000)
    print(json.dumps([{**asdict(d), "latency_ms": d.latency_ms} for d in mon.detections]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
