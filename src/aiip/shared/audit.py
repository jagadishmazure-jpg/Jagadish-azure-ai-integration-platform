"""Append-only, hash-chained audit log. Each record answers: who (actor + subject) did what
(operation, side effect) to which business key, when, with what result, under which trace.

Arguments are stored as a digest, not verbatim, to keep business data out of the audit store.
In Azure the same record is also emitted as a span event, landing in Application Insights.

Trusted telemetry: every record is also signed (Ed25519) by the writing service, so a verifier
holding only the public key can tell a genuine record from one that was edited, inserted or
re-chained by someone who does not hold the signing key. The key comes from a `kv://` reference
(local stand-in vault offline; in Azure it would be a Key Vault key with a sign-only role).

Readers that sit outside the request path (the out-of-band monitor) subscribe with a wake-up
callback. The writer only flips that flag; all inspection happens on the reader's side."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from opentelemetry import trace

GENESIS = "0" * 16
UNHASHED = {"hash", "sig"}  # the signature covers the hash, so it cannot be part of it
SIGNING_KEY_REF = "kv://aiip-local-kv/telemetry-signing-key"


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


def record_digest(rec: dict[str, Any]) -> str:
    return digest({k: v for k, v in rec.items() if k not in UNHASHED})


class Signer:
    """Ed25519 signer whose private key is derived from a vault secret on first use."""

    def __init__(self, key_ref: str = SIGNING_KEY_REF) -> None:
        self.key_ref = key_ref
        self._key: Ed25519PrivateKey | None = None
        self._lock = threading.Lock()

    def _private(self) -> Ed25519PrivateKey:
        with self._lock:
            if self._key is None:
                from aiip.shared.secrets import RESOLVER

                seed = hashlib.sha256(RESOLVER.resolve(self.key_ref).encode()).digest()
                self._key = Ed25519PrivateKey.from_private_bytes(seed)
            return self._key

    def public_bytes(self) -> bytes:
        return (
            self._private()
            .public_key()
            .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        )

    @property
    def key_id(self) -> str:
        return hashlib.sha256(self.public_bytes()).hexdigest()[:12]

    def sign(self, record_hash: str) -> str:
        return self._private().sign(record_hash.encode()).hex()


SIGNER = Signer()


def verify_signature(public_key: bytes, record_hash: str, sig: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(bytes.fromhex(sig), record_hash.encode())
        return True
    except (InvalidSignature, ValueError):
        return False


def verify_records(
    records: list[dict[str, Any]], public_key: bytes | None = None, start_prev: str = GENESIS
) -> tuple[bool, int | None, str]:
    """Check chain links, record hashes and (if a public key is given) signatures.
    Returns (ok, index of the first bad record or None, reason)."""
    prev = start_prev
    for i, r in enumerate(records):
        if r.get("prev_hash") != prev:
            return False, i, "broken chain link"
        if r.get("hash") != record_digest(r):
            return False, i, "record hash mismatch"
        if public_key is not None and not verify_signature(public_key, r["hash"], r.get("sig", "")):
            return False, i, "bad or missing signature"
        prev = r["hash"]
    return True, None, "ok"


class AuditLog:
    def __init__(self, name: str, signer: Signer | None = SIGNER) -> None:
        self.name = name
        self.signer = signer
        self.records: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._listeners: list[Callable[[], None]] = []

    def write(self, **fields: Any) -> dict[str, Any]:
        with self._lock:
            prev = self.records[-1]["hash"] if self.records else GENESIS
            rec = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "log": self.name, **fields}
            rec["t_mono_ns"] = time.monotonic_ns()
            if self.signer is not None:
                rec["key_id"] = self.signer.key_id
            rec["prev_hash"] = prev
            rec["hash"] = record_digest(rec)
            if self.signer is not None:
                rec["sig"] = self.signer.sign(rec["hash"])
            self.records.append(rec)
        for wake in list(self._listeners):
            wake()  # a flag flip only; readers do their own work on their own thread
        span = trace.get_current_span()
        if span.is_recording():
            span.add_event("audit", {k: str(v) for k, v in rec.items() if v is not None})
        sink = os.environ.get("AIIP_AUDIT_DIR")
        if sink:
            os.makedirs(sink, exist_ok=True)
            with open(os.path.join(sink, f"{self.name}.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        return rec

    def subscribe(self, wake: Callable[[], None]) -> None:
        self._listeners.append(wake)

    def unsubscribe(self, wake: Callable[[], None]) -> None:
        if wake in self._listeners:
            self._listeners.remove(wake)

    def read_from(self, index: int) -> list[dict[str, Any]]:
        """Read-only copy of records from `index` on (used by out-of-band readers)."""
        with self._lock:
            return [dict(r) for r in self.records[index:]]

    def query(self, **filters: Any) -> list[dict[str, Any]]:
        return [r for r in self.records if all(r.get(k) == v for k, v in filters.items() if v is not None)]

    def public_key(self) -> bytes | None:
        return self.signer.public_bytes() if self.signer else None

    def verify_chain(self) -> bool:
        return verify_records(self.records, self.public_key())[0]

    def reset(self) -> None:
        self.records.clear()
