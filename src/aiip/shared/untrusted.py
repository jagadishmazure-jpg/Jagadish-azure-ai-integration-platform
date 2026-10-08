"""Screening for content produced outside our trust boundary (MCP server output, foreign agent
artifacts, SaaS free text). Instruction-like strings are withheld before anything reaches a model;
the caller gets a flag and the field path, never the payload.

Two layers. The regex screen below always runs and is the only layer offline, in tests and in CI.
With ``AIIP_PROMPT_SHIELDS=1`` and ``AZURE_CONTENT_SAFETY_ENDPOINT`` set, every string the regex
screen let through is also sent to Azure AI Content Safety Prompt Shields
(:mod:`aiip.shared.content_safety`); strings it flags, or every string in a batch it could not
screen, are withheld and flagged with the pattern ``prompt_shields``."""

from __future__ import annotations

import re
from typing import Any

from aiip.shared import content_safety

PATTERNS = {
    "override": re.compile(
        r"\b(ignore|disregard|forget)\b.{0,40}\b(previous|prior|above|all)\b.{0,20}\b(instructions?|rules?|prompts?)\b",
        re.I | re.S,
    ),
    "prompt_exfil": re.compile(
        r"\b(reveal|print|show|leak)\b.{0,30}\b(system prompt|instructions|secrets?|api key|token)\b",
        re.I | re.S,
    ),
    "tool_steering": re.compile(
        r"\b(call|invoke|run|execute)\b.{0,20}\b(tool|function|create_\w+|delete_\w+|update_\w+)\b",
        re.I | re.S,
    ),
    "role_spoof": re.compile(r"(^|\n)\s*(system|assistant|developer)\s*:", re.I),
    "markup": re.compile(r"<\s*(script|iframe|img[^>]+onerror)", re.I),
}
WITHHELD = "[withheld: instruction-like content from an untrusted source]"
MAX_STRING = 4000


def screen(value: Any, path: str = "") -> tuple[Any, list[dict[str, str]]]:
    """Regex screen, then Prompt Shields when it is switched on. Returns (clean value, flags)."""
    clean, flags = _regex_screen(value, path)
    leaves: list[tuple[str, str]] = []
    _string_leaves(clean, path, leaves)
    todo = [(p, s) for p, s in leaves if s.strip() and s != WITHHELD]
    attacked = content_safety.shield([s for _, s in todo])
    if not attacked:
        return clean, flags
    hit = {p for (p, _), bad in zip(todo, attacked, strict=True) if bad}
    flags += [{"path": p or "/", "patterns": "prompt_shields"} for p, _ in todo if p in hit]
    return _withhold(clean, path, hit), flags


def _string_leaves(value: Any, path: str, out: list[tuple[str, str]]) -> None:
    if isinstance(value, str):
        out.append((path, value))
    elif isinstance(value, dict):
        for k, v in value.items():
            _string_leaves(v, f"{path}/{k}", out)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _string_leaves(v, f"{path}/{i}", out)


def _withhold(value: Any, path: str, hit: set[str]) -> Any:
    if isinstance(value, str):
        return content_safety.SHIELDED if path in hit else value
    if isinstance(value, dict):
        return {k: _withhold(v, f"{path}/{k}", hit) for k, v in value.items()}
    if isinstance(value, list):
        return [_withhold(v, f"{path}/{i}", hit) for i, v in enumerate(value)]
    return value


def _regex_screen(value: Any, path: str = "") -> tuple[Any, list[dict[str, str]]]:
    flags: list[dict[str, str]] = []
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[k], f = _regex_screen(v, f"{path}/{k}")
            flags += f
        return out, flags
    if isinstance(value, list):
        out_l = []
        for i, v in enumerate(value):
            nv, f = _regex_screen(v, f"{path}/{i}")
            out_l.append(nv)
            flags += f
        return out_l, flags
    if isinstance(value, str):
        hits = [name for name, rx in PATTERNS.items() if rx.search(value)]
        if hits:
            return WITHHELD, [{"path": path or "/", "patterns": ",".join(hits)}]
        if len(value) > MAX_STRING:
            return value[:MAX_STRING] + "...[truncated]", [{"path": path or "/", "patterns": "oversize"}]
    return value, flags
