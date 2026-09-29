"""Child-side bootstrap for the local subprocess sandbox. Standard library only; started as
`python -I -S sandbox_runner.py` with a clean environment and a fresh working directory.

Before any tool code runs it (1) sets hard resource limits, (2) pre-imports what it needs, and
(3) installs a Python audit hook that refuses network sockets, process creation, native-code
loading and file access outside the declared read/write roots. Hard limits and the audit hook
cannot be lifted from inside the process. Violations are recorded by the hook itself, so tool
code that catches the PermissionError still gets reported.

Audit hooks are a tripwire layer, not a kernel boundary. The parent adds a network namespace when
the host allows unprivileged ones, and production runs use a container or a dynamic session."""

import builtins
import contextlib
import io
import json
import os
import resource
import sys
import traceback

NET = {
    "socket.__new__",
    "socket.connect",
    "socket.bind",
    "socket.getaddrinfo",
    "socket.sendto",
    "socket.sendmsg",
}
PROC = {
    "subprocess.Popen",
    "os.system",
    "os.exec",
    "os.posix_spawn",
    "os.spawn",
    "os.fork",
    "os.forkpty",
    "os.kill",
    "os.killpg",
    "pty.spawn",
    "ctypes.dlopen",
    "ctypes.dlsym",
    "sys._getframe",
    "sys._current_frames",
}
NO_IMPORT = {"ctypes", "_ctypes", "gc", "_posixsubprocess", "cffi", "_testcapi", "_xxsubinterpreters"}
PATH_EVENTS = {
    "os.remove": True,
    "os.rename": True,
    "os.rmdir": True,
    "os.mkdir": True,
    "os.chmod": True,
    "os.chown": True,
    "os.symlink": True,
    "os.link": True,
    "os.truncate": True,
    "os.utime": True,
    "os.listdir": False,
    "os.scandir": False,
    "os.chdir": False,
}
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def main() -> None:
    cfg = json.loads(sys.stdin.read())
    out = sys.__stdout__
    lim = cfg["limits"]
    for res, value in (
        (resource.RLIMIT_CPU, lim["cpu_s"]),
        (resource.RLIMIT_AS, lim["memory_mb"] * 1024 * 1024),
        (resource.RLIMIT_FSIZE, lim["max_file_mb"] * 1024 * 1024),
        (resource.RLIMIT_NOFILE, 64),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(res, (value, value))

    real = os.path.realpath
    read_roots = [real(p) for p in cfg["read_roots"]]
    write_roots = [real(p) for p in cfg["write_roots"]]
    system_roots = [real(p) for p in cfg["system_roots"]]
    violations: list[str] = []

    def inside(path: str, roots: list[str]) -> bool:
        return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)

    def refuse(msg: str) -> None:
        violations.append(msg)
        raise PermissionError("sandbox: " + msg)

    def hook(event: str, args: tuple) -> None:
        if event == "open":
            path, mode, flags = [*list(args), None, None, None][:3]
            if path is None or isinstance(path, int):
                return
            p = real(os.fsdecode(path))
            wants_write = bool(mode and any(c in str(mode) for c in "wax+")) or bool(
                (flags or 0) & WRITE_FLAGS
            )
            allowed = write_roots if wants_write else read_roots + write_roots + system_roots
            if not inside(p, allowed):
                refuse(f"{'write' if wants_write else 'read'} outside scope: {p}")
        elif event in PATH_EVENTS and args and isinstance(args[0], str | bytes):
            p = real(os.fsdecode(args[0]))
            allowed = write_roots if PATH_EVENTS[event] else read_roots + write_roots + system_roots
            if not inside(p, allowed):
                refuse(f"{event} outside scope: {p}")
        elif event in NET and not cfg["allow_network"]:
            refuse(f"network denied ({event})")
        elif event in PROC:
            refuse(f"process/native access denied ({event})")
        elif event == "import" and args and str(args[0]).split(".")[0] in NO_IMPORT:
            refuse(f"import denied ({args[0]})")

    sys.addaudithook(hook)
    captured = io.StringIO()
    result: dict = {"nonce": cfg["nonce"], "ok": True, "result": None, "error": None}
    scope = {"ARGS": cfg["args"], "RESULT": None, "__builtins__": builtins, "__name__": "__sandbox__"}
    try:
        with contextlib.redirect_stdout(captured):
            exec(compile(cfg["code"], "<sandboxed-tool>", "exec"), scope)
        result["result"] = scope.get("RESULT")
    except MemoryError:
        result.update(ok=False, error="memory_limit")
    except BaseException as exc:
        result.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:300])
        result["trace"] = traceback.format_exc(limit=2)[-600:]
    result["violations"] = violations
    result["stdout"] = captured.getvalue()[-4000:]
    try:
        line = json.dumps(result, default=str)
    except (TypeError, ValueError):
        result["result"] = repr(result["result"])[:2000]
        line = json.dumps(result, default=str)
    out.write("\n" + line + "\n")
    out.flush()


if __name__ == "__main__":
    main()
