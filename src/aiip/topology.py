"""Launch the whole integration plane locally as separate processes talking real HTTP.

One process per service, the same split as Container Apps: five gateways, the BPM host, three MCP
servers, five A2A agents and the SaaS stand-ins. Every process gets the full `AIIP_*_URL` map, so
all traffic between them is HTTP on 127.0.0.1 (nothing in-process)."""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from aiip.shared.http import env_name

PY = sys.executable


@dataclass(frozen=True)
class Service:
    name: str
    port: int
    argv: tuple[str, ...]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def _uv(app: str, port: int) -> tuple[str, ...]:
    return (
        PY,
        "-m",
        "uvicorn",
        app,
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
        "--no-access-log",
    )


SERVICES: tuple[Service, ...] = (
    Service("identity", 8401, _uv("aiip.identity.gateway:app", 8401)),
    Service("tool-gateway", 8402, _uv("aiip.tools.gateway:app", 8402)),
    Service("mcp-gateway", 8403, _uv("aiip.mcp.gateway:app", 8403)),
    Service("a2a-gateway", 8404, _uv("aiip.a2a.gateway:app", 8404)),
    Service("event-gateway", 8405, _uv("aiip.events.gateway:app", 8405)),
    Service("bpm-host", 8406, _uv("aiip.bpm.host:app", 8406)),
    Service("mcp-sap-orders", 8411, (PY, "-m", "aiip.mcp_servers", "sap-orders")),
    Service("mcp-servicenow-incidents", 8412, (PY, "-m", "aiip.mcp_servers", "servicenow-incidents")),
    Service("mcp-sql-warehouse", 8413, (PY, "-m", "aiip.mcp_servers", "sql-warehouse")),
    Service("agent-care-planner", 8421, (PY, "-m", "aiip.agents", "care-planner")),
    Service("agent-crm", 8422, (PY, "-m", "aiip.agents", "crm-agent")),
    Service("agent-erp", 8423, (PY, "-m", "aiip.agents", "erp-agent")),
    Service("agent-data", 8424, (PY, "-m", "aiip.agents", "data-agent")),
    Service("agent-ap-invoice", 8425, (PY, "-m", "aiip.agents", "ap-invoice-agent")),
    Service("fake-saas", 8431, _uv("aiip.fakesaas.app:app", 8431)),
)


def topology_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base if base is not None else os.environ)
    env.setdefault("AIIP_MODE", "local")
    env.setdefault("AIIP_LOCAL_VAULT_SEED", secrets.token_hex(16))
    for s in SERVICES:
        env[env_name(s.name)] = s.url
    return env


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


class Topology:
    """Context manager: start all services, wait for their ports, stop them on exit."""

    def __init__(self, log_dir: str | Path = ".demo-logs") -> None:
        self.log_dir = Path(log_dir)
        self.procs: list[tuple[Service, subprocess.Popen]] = []
        self.env = topology_env()

    def __enter__(self) -> Topology:
        busy = [s.port for s in SERVICES if _port_open(s.port)]
        if busy:
            raise RuntimeError(f"ports already in use: {busy}")
        self.log_dir.mkdir(exist_ok=True)
        for s in SERVICES:
            env = {**self.env, "PORT": str(s.port), "HOST": "127.0.0.1"}
            # The child keeps its own copy of the file descriptor, so the parent can close it at once.
            with open(self.log_dir / f"{s.name}.log", "w") as log:
                self.procs.append(
                    (s, subprocess.Popen(s.argv, env=env, stdout=log, stderr=subprocess.STDOUT))
                )
        deadline = time.monotonic() + 60
        pending = list(SERVICES)
        while pending and time.monotonic() < deadline:
            for s, p in self.procs:
                if p.poll() is not None:
                    raise RuntimeError(f"{s.name} exited early; see {self.log_dir / (s.name + '.log')}")
            pending = [s for s in pending if not _port_open(s.port)]
            time.sleep(0.2)
        if pending:
            raise RuntimeError(f"services did not start: {[s.name for s in pending]}")
        os.environ.update(self.env)  # the calling process (demo, workers) uses the same URLs + vault seed
        return self

    def __exit__(self, *exc) -> None:
        for _, p in self.procs:
            p.terminate()
        for _, p in self.procs:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()


def main() -> None:  # pragma: no cover - manual use: keep the topology up until Ctrl+C
    with Topology() as t:
        for s in SERVICES:
            print(f"{s.name:28s} {s.url}")
        print("topology up; Ctrl+C to stop")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    _ = t


if __name__ == "__main__":  # pragma: no cover
    main()
