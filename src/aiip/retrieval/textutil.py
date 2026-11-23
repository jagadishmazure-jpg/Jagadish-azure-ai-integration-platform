"""Token helpers shared by the hybrid index and the knowledge graph. Deterministic, no model."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def tokens(text: str) -> list[str]:
    """Lowercase word tokens. Hyphenated identifiers such as ``EU-COATING-2026`` stay one token."""
    return _TOKEN.findall(text.lower())
