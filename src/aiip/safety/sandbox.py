"""Agent sandbox: tool code runs in an isolated executor, never in the agent's process.

Two executors behind one interface (`Sandbox.run(code, args, spec, session_id)`):

* `SubprocessSandbox` (local, tested): a fresh `python -I -S` child per call with an empty
  working directory, an environment built from an allow-list, hard CPU / memory / file-size /
  open-file limits, a wall-clock timeout that kills the whole process group, deny-by-default
  networking and file access limited to the declared read/write roots (see sandbox_runner.py).
  When the host permits unprivileged user namespaces the child is also started inside an empty
  network namespace (`unshare -rn`), so networking is refused by the kernel as well; the result
  lists which isolation layers were active.
* `ContainerAppsSessions` (stub): Azure Container Apps dynamic sessions, where each session is a
  Hyper-V isolated sandbox in a pool whose egress is disabled at the pool level. The request
  shape is implemented and unit-tested against a mock transport; it has not been run against a
  live pool.

`SANDBOX_TOOLS` are the tools that execute here (as opposed to the Tool Gateway's connector
tools, which reach systems of record through the gateway)."""

from __future__ import annotations

import asyncio
import functools
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

RUNNER = Path(__file__).with_name("sandbox_runner.py")


@dataclass(frozen=True)
class SandboxSpec:
    read_paths: tuple[str, ...] = ()
    write_paths: tuple[str, ...] = ()  # the per-call working directory is always writable
    env_allow: tuple[str, ...] = ("LANG", "LC_ALL", "TZ")
    allow_network: bool = False
    cpu_s: int = 2
    wall_s: float = 5.0
    memory_mb: int = 256
    max_file_mb: int = 8
    max_output_bytes: int = 64_000


@dataclass
class SandboxResult:
    ok: bool
    result: Any = None
    error: str | None = None
    violations: list[str] = field(default_factory=list)
    stdout: str = ""
    limit: str | None = None  # "wall_time" | "cpu_time" | "memory" when a limit ended the run
    exit_code: int | None = None
    duration_ms: float = 0.0
    isolation: tuple[str, ...] = ()

    @property
    def result_class(self) -> str:
        if self.violations:
            return "sandbox_violation"
        if self.limit:
            return "sandbox_limit"
        return "ok" if self.ok else "sandbox_error"


class Sandbox(Protocol):
    name: str

    async def run(
        self, code: str, args: dict[str, Any], spec: SandboxSpec, session_id: str = ""
    ) -> SandboxResult:
        """Run ``code`` with ``args`` under ``spec`` and return what happened."""


class SandboxUnavailable(RuntimeError):
    pass


@functools.cache
def netns_available() -> bool:
    exe = shutil.which("unshare")
    if not exe or os.environ.get("AIIP_SANDBOX_NETNS", "auto") == "off":
        return False
    try:
        return subprocess.run([exe, "-rn", "true"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _system_roots() -> list[str]:
    paths = sysconfig.get_paths()
    roots = {paths["stdlib"], paths["platstdlib"], str(RUNNER.parent)}
    roots |= {os.path.join(sys.base_prefix, "lib"), os.path.join(sys.base_exec_prefix, "lib")}
    return sorted(r for r in roots if r)


class SubprocessSandbox:
    name = "local-subprocess"

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = base_dir

    def _run_sync(self, code: str, args: dict[str, Any], spec: SandboxSpec) -> SandboxResult:
        work = tempfile.mkdtemp(prefix="aiip-sbx-", dir=self.base_dir)
        nonce = secrets.token_hex(8)
        cfg = {
            "nonce": nonce,
            "code": code,
            "args": args,
            "allow_network": spec.allow_network,
            "read_roots": list(spec.read_paths),
            "write_roots": [work, *spec.write_paths],
            "system_roots": _system_roots(),
            "limits": {"cpu_s": spec.cpu_s, "memory_mb": spec.memory_mb, "max_file_mb": spec.max_file_mb},
        }
        env = {k: os.environ[k] for k in spec.env_allow if k in os.environ}
        env["HOME"] = work
        env["PATH"] = "/usr/bin:/bin"  # exec is refused anyway; nothing from the parent's PATH
        layers = ["process", "rlimits", "clean-env", "scoped-fs", "audit-hooks"]
        cmd = [sys.executable, "-I", "-S", "-B", str(RUNNER)]
        if not spec.allow_network and netns_available():
            cmd = [shutil.which("unshare") or "unshare", "-rn", *cmd]
            layers.append("netns")
        t0 = time.perf_counter()
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=work,
            env=env,
            start_new_session=True,
        )
        limit = None
        try:
            out, err = proc.communicate(json.dumps(cfg).encode(), timeout=spec.wall_s)
        except subprocess.TimeoutExpired:
            with _suppress():
                os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
            limit = "wall_time"
        finally:
            shutil.rmtree(work, ignore_errors=True)
        ms = (time.perf_counter() - t0) * 1000
        rc = proc.returncode
        if limit is None and rc in (-signal.SIGXCPU, -signal.SIGKILL, 128 + signal.SIGXCPU):
            limit = "cpu_time"
        payload = None
        for line in reversed(out[-spec.max_output_bytes :].decode(errors="replace").splitlines()):
            if line.startswith('{"nonce": "' + nonce + '"'):
                payload = json.loads(line)
                break
        if payload is None:
            return SandboxResult(
                False,
                error=limit or f"sandbox exited with {rc}: {err.decode(errors='replace')[-200:]}",
                limit=limit,
                exit_code=rc,
                duration_ms=ms,
                isolation=tuple(layers),
            )
        if payload.get("error") == "memory_limit":
            limit = "memory"
        return SandboxResult(
            bool(payload["ok"]),
            payload.get("result"),
            payload.get("error"),
            list(payload.get("violations") or []),
            payload.get("stdout", ""),
            limit,
            rc,
            ms,
            tuple(layers),
        )

    async def run(
        self, code: str, args: dict[str, Any], spec: SandboxSpec, session_id: str = ""
    ) -> SandboxResult:
        return await asyncio.to_thread(self._run_sync, code, args, spec)


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return exc[0] is not None and issubclass(exc[0], ProcessLookupError | PermissionError)


class ContainerAppsSessions:
    """Adapter for Azure Container Apps dynamic sessions (code-interpreter pools).

    `POST {pool}/code/execute?api-version=...&identifier={session}` with a bearer token for the
    `https://dynamicsessions.io` audience (the workload's managed identity needs the
    "Azure ContainerApps Session Executor" role on the pool). Network isolation and CPU/memory
    come from the pool definition (egress disabled, per-session limits), so `spec.allow_network`
    must stay False and file scoping is the session's own disk. Not exercised against Azure."""

    name = "aca-dynamic-sessions"
    API_VERSION = "2024-02-02-preview"
    SCOPE = "https://dynamicsessions.io/.default"

    def __init__(
        self,
        pool_endpoint: str | None = None,
        token_provider=None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.pool_endpoint = (pool_endpoint or os.environ.get("AIIP_ACA_SESSION_POOL_ENDPOINT", "")).rstrip(
            "/"
        )
        self.token_provider = token_provider
        self.transport = transport

    def build_request(self, code: str, args: dict[str, Any], session_id: str) -> dict[str, Any]:
        prelude = f"import json\nARGS = json.loads({json.dumps(json.dumps(args))})\nRESULT = None\n"
        epilogue = "\nprint(json.dumps({'result': RESULT}, default=str))\n"
        return {
            "method": "POST",
            "url": f"{self.pool_endpoint}/code/execute",
            "params": {"api-version": self.API_VERSION, "identifier": session_id},
            "json": {
                "properties": {
                    "codeInputType": "inline",
                    "executionType": "synchronous",
                    "code": prelude + code + epilogue,
                }
            },
        }

    def _token(self) -> str:
        if self.token_provider is not None:
            return self.token_provider()
        from aiip.config import is_azure

        if not is_azure():
            raise SandboxUnavailable("dynamic sessions need Azure mode and a session pool endpoint")
        from azure.identity import DefaultAzureCredential  # pragma: no cover - Azure only

        return DefaultAzureCredential().get_token(self.SCOPE).token  # pragma: no cover

    async def run(
        self, code: str, args: dict[str, Any], spec: SandboxSpec, session_id: str = ""
    ) -> SandboxResult:
        if not self.pool_endpoint:
            raise SandboxUnavailable("AIIP_ACA_SESSION_POOL_ENDPOINT is not set")
        if spec.allow_network:
            raise SandboxUnavailable("network access is decided by the session pool, not per call")
        req = self.build_request(code, args, session_id or secrets.token_hex(8))
        headers = {"authorization": f"Bearer {self._token()}"}
        t0 = time.perf_counter()
        async with httpx.AsyncClient(transport=self.transport, timeout=spec.wall_s + 5) as c:
            r = await c.request(
                req["method"], req["url"], params=req["params"], json=req["json"], headers=headers
            )
        ms = (time.perf_counter() - t0) * 1000
        if r.status_code >= 400:
            return SandboxResult(False, error=f"session pool returned HTTP {r.status_code}", duration_ms=ms)
        props = r.json().get("properties", {})
        stdout = props.get("stdout", "") or ""
        result = None
        for line in reversed(stdout.splitlines()):
            if line.startswith('{"result"'):
                result = json.loads(line)["result"]
                break
        ok = props.get("status", "Success") == "Success" and not props.get("stderr")
        return SandboxResult(
            ok,
            result,
            props.get("stderr") or None,
            stdout=stdout[-4000:],
            duration_ms=ms,
            isolation=("hyper-v-session", "pool-egress-policy"),
        )


# Tools that execute inside the sandbox. `code` receives ARGS and sets RESULT.
SANDBOX_TOOLS: dict[str, dict[str, Any]] = {
    "sandbox.python": {
        "description": "Run agent-generated Python over ARGS['input']; the code is ARGS['code'].",
        "code": None,  # supplied per call
    },
    "sandbox.csv_stats": {
        "description": "Row count and column total for a CSV inside the declared data scope.",
        "code": (
            "import csv\n"
            "with open(ARGS['path'], newline='') as f:\n"
            "    rows = list(csv.DictReader(f))\n"
            "col = ARGS.get('column')\n"
            "RESULT = {'rows': len(rows), 'total': round(sum(float(r[col]) for r in rows), 2) if col else None}\n"
        ),
    },
}
