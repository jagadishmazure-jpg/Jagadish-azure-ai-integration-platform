"""Local token issuer: an Entra ID stand-in for offline runs.

It mints RS256 access tokens that follow the Entra v2 shape (iss, aud, tid, oid, sub, azp, scp or
roles, ver=2.0) so the validators are exercised exactly as in Azure. The RSA key is generated in
memory at start-up and never written anywhere. Disabled in AIIP_MODE=azure."""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from aiip.config import issuer_for

TOKEN_LIFETIME_S = 600


class LocalIssuer:
    def __init__(self) -> None:
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = uuid.uuid4().hex[:16]

    def jwks(self) -> dict[str, Any]:
        jwk = RSAAlgorithm.to_jwk(self._key.public_key(), as_dict=True)
        jwk.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return {"keys": [jwk]}

    def mint(self, claims: dict[str, Any], lifetime_s: int = TOKEN_LIFETIME_S) -> str:
        now = int(time.time())
        body = {
            "iss": issuer_for(claims["tid"]),
            "iat": now,
            "nbf": now,
            "exp": now + lifetime_s,
            "ver": "2.0",
            "uti": uuid.uuid4().hex[:22],
            **claims,
        }
        return jwt.encode(body, self._key, algorithm="RS256", headers={"kid": self.kid})


ISSUER = LocalIssuer()
