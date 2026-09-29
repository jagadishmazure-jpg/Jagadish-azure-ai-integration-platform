"""Payload classification shared by the policy prover (inline) and the out-of-band monitor.

Only class names ever leave this module; matched values are never logged or returned. Injection
markers reuse the patterns in `aiip.shared.untrusted` so there is one definition of
"instruction-like text from outside the trust boundary"."""

from __future__ import annotations

import json
import re
from typing import Any

from aiip.shared.untrusted import PATTERNS as INJECTION_PATTERNS

SENSITIVE: dict[str, re.Pattern[str]] = {
    "pii.us_ssn": re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"),
    "pii.payment_card": re.compile(r"\b(?:\d[ -]?){13,19}\b"),
    "secret.jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    "secret.private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "secret.assignment": re.compile(
        r"\b(api[_-]?key|client[_-]?secret|password|passwd|access[_-]?token)\b\s*[:=]\s*['\"]?[^\s'\"]{8,}",
        re.I,
    ),
    "secret.conn_string": re.compile(r"(AccountKey|SharedAccessKey|Password)=[^;\s]{8,}", re.I),
}
BLOCKING = frozenset(SENSITIVE)  # every class above blocks egress; kept separate so policy can widen/narrow


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total, alt = total + d, not alt
    return total % 10 == 0


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def classify(value: Any) -> list[str]:
    """Sensitive-data classes present anywhere in `value` (sorted, de-duplicated)."""
    text = _text(value)
    found = set()
    for name, rx in SENSITIVE.items():
        for m in rx.finditer(text):
            if name == "pii.payment_card":
                digits = re.sub(r"\D", "", m.group())
                if not (13 <= len(digits) <= 19 and _luhn(digits)):
                    continue
            found.add(name)
            break
    return sorted(found)


def injection_markers(value: Any) -> list[str]:
    text = _text(value)
    return sorted(name for name, rx in INJECTION_PATTERNS.items() if rx.search(text))
