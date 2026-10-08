"""Key Vault secret references. Configuration only ever contains `kv://<vault>/<secret-name>`;
the value is resolved at call time and never logged, cached in files or returned to an agent.

* azure: `azure.keyvault.secrets.SecretClient` with the workload's managed identity.
* local: a stand-in vault whose values are derived (scrypt KDF) from AIIP_LOCAL_VAULT_SEED, which the
  demo generates fresh on every run. There is no secret material in the repository."""

from __future__ import annotations

import hashlib
import os
import re
import secrets as _secrets
import time
from dataclasses import dataclass

from aiip.config import is_azure

LOGICAL_VAULT = "aiip-local-kv"
_REF = re.compile(r"^kv://([a-z0-9-]{3,24})/([A-Za-z0-9-]{1,127})$")
_SENSITIVE_KEYS = re.compile(r"(secret|password|passwd|api[_-]?key|token|private[_-]?key)", re.I)


class SecretRefError(ValueError):
    pass


@dataclass(frozen=True)
class SecretRef:
    vault: str
    name: str

    @classmethod
    def parse(cls, ref: str) -> SecretRef:
        m = _REF.match(ref or "")
        if not m:
            raise SecretRefError("not a kv:// secret reference")
        return cls(m.group(1), m.group(2))

    def __str__(self) -> str:
        return f"kv://{self.vault}/{self.name}"


def assert_refs_only(config: dict, path: str = "") -> None:
    """Fail if a config value under a sensitive-looking key is anything other than a kv:// ref."""
    for k, v in config.items():
        here = f"{path}.{k}" if path else k
        if isinstance(v, dict):
            assert_refs_only(v, here)
        elif isinstance(v, str) and _SENSITIVE_KEYS.search(k) and not k.endswith("_ref"):
            raise SecretRefError(f"{here}: sensitive key must be a *_ref pointing at Key Vault")
        elif isinstance(v, str) and k.endswith("_ref"):
            SecretRef.parse(v)


class SecretResolver:
    def __init__(self, ttl_s: float = 300.0) -> None:
        self.ttl_s = ttl_s
        self._cache: dict[str, tuple[float, str]] = {}
        self._clients: dict[str, object] = {}
        self.resolutions = 0

    def _local_value(self, ref: SecretRef) -> str:
        seed = os.environ.setdefault("AIIP_LOCAL_VAULT_SEED", _secrets.token_hex(32))
        # Deterministic stand-in value per reference, derived with scrypt (a memory-hard KDF) from a
        # per-process seed. Nothing here is stored or compared as a password; the cost is kept low.
        return hashlib.scrypt(str(ref).encode(), salt=seed.encode(), n=2**10, r=8, p=1, dklen=32).hex()

    def _azure_value(self, ref: SecretRef) -> str:  # pragma: no cover - needs Azure
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient

        # `aiip-local-kv` is the logical vault name used in code; the deployed vault name comes from Bicep
        vault = os.environ.get("AIIP_KEYVAULT_NAME", ref.vault) if ref.vault == LOGICAL_VAULT else ref.vault
        client = self._clients.get(vault)
        if client is None:
            client = SecretClient(f"https://{vault}.vault.azure.net", DefaultAzureCredential())
            self._clients[vault] = client
        return client.get_secret(ref.name).value  # type: ignore[attr-defined]

    def resolve(self, ref: str | SecretRef) -> str:
        r = ref if isinstance(ref, SecretRef) else SecretRef.parse(ref)
        key = str(r)
        hit = self._cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        self.resolutions += 1
        value = self._azure_value(r) if is_azure() else self._local_value(r)
        self._cache[key] = (time.monotonic() + self.ttl_s, value)
        return value


RESOLVER = SecretResolver()
