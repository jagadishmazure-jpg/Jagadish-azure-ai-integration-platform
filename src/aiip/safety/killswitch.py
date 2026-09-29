"""Kill switch. The out-of-band monitor writes quarantines here; the Tool, MCP, A2A and Event
gateways (and the supervisor) read it before doing anything for a caller.

Scopes: a session (one agent run) or a whole actor (every run of that agent), always within a
tenant. Two backends, both consulted on every check:

* in-memory, for a monitor thread inside the same process (tests, the in-process demo);
* an append-only JSONL file named by `AIIP_KILLSWITCH_FILE`, so a monitor running as a separate
  process can quarantine callers of gateways it shares nothing else with. The file is re-read only
  when its size changes. In Azure this would be a shared store the gateways cache for a second
  (for example an APIM named value or a small Cosmos DB container); that part is not built here."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from aiip.shared import errors as E


@dataclass(frozen=True)
class Quarantine:
    tenant: str
    scope: str  # "session" | "actor"
    key: str
    detector: str
    reason: str
    evidence: str  # hash of the record that triggered it
    at_ns: int
    at: str

    def public(self) -> dict[str, Any]:
        return {"scope": self.scope, "key": self.key, "detector": self.detector, "at": self.at}


class KillSwitch:
    def __init__(self) -> None:
        self._mem: dict[tuple[str, str, str], Quarantine] = {}
        self._file: dict[tuple[str, str, str], Quarantine] = {}
        self._file_size = -1
        self._lock = threading.Lock()

    @staticmethod
    def _path() -> str | None:
        return os.environ.get("AIIP_KILLSWITCH_FILE") or None

    def quarantine(
        self,
        *,
        tenant: str,
        scope: str,
        key: str,
        detector: str,
        reason: str,
        evidence: str = "",
        to_file: bool | None = None,
    ) -> Quarantine:
        if scope not in {"session", "actor"} or not key:
            raise ValueError("quarantine needs scope session|actor and a key")
        q = Quarantine(
            tenant,
            scope,
            key,
            detector,
            reason[:200],
            evidence,
            time.monotonic_ns(),
            datetime.now(UTC).isoformat(timespec="milliseconds"),
        )
        with self._lock:
            self._mem.setdefault((tenant, scope, key), q)
        path = self._path()
        if path and (to_file is None or to_file):
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(q)) + "\n")
                f.flush()
                os.fsync(f.fileno())
        return self._mem[(tenant, scope, key)]

    def _load_file(self) -> None:
        path = self._path()
        if not path or not os.path.exists(path):
            return
        size = os.path.getsize(path)
        if size == self._file_size:
            return
        entries: dict[tuple[str, str, str], Quarantine] = {}
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    q = Quarantine(**json.loads(line))
                except (ValueError, TypeError):
                    continue  # a torn last line is picked up on the next read
                entries.setdefault((q.tenant, q.scope, q.key), q)
        self._file, self._file_size = entries, size

    def check(
        self, *, tenant: str, actor: str | None = None, session: str | None = None
    ) -> Quarantine | None:
        self._load_file()
        for scope, key in (("session", session), ("actor", actor)):
            if key:
                hit = self._mem.get((tenant, scope, key)) or self._file.get((tenant, scope, key))
                if hit:
                    return hit
        return None

    def enforce(self, tenant: str, actor: str, session: str | None) -> None:
        """Raise the sanitized `quarantined` error if this caller is contained."""
        q = self.check(tenant=tenant, actor=actor, session=session)
        if q:
            raise E.GatewayError(
                E.QUARANTINED, f"{q.scope} quarantined by the runtime monitor", detail=q.public()
            )

    def entries(self) -> list[Quarantine]:
        self._load_file()
        return list({**self._file, **self._mem}.values())

    def release(self, tenant: str, scope: str, key: str) -> bool:
        """Human release after review (in-memory entries only; file entries are an audit record)."""
        with self._lock:
            return self._mem.pop((tenant, scope, key), None) is not None

    def reset(self) -> None:
        with self._lock:
            self._mem.clear()
            self._file.clear()
            self._file_size = -1


KILL = KillSwitch()
