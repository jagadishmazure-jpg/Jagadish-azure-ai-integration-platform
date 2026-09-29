"""Screening for content produced outside our trust boundary (MCP server output, foreign agent
artifacts, SaaS free text). Instruction-like strings are withheld before anything reaches a model;
the caller gets a flag and the field path, never the payload."""

from __future__ import annotations

import re
from typing import Any

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
    flags: list[dict[str, str]] = []
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out[k], f = screen(v, f"{path}/{k}")
            flags += f
        return out, flags
    if isinstance(value, list):
        out_l = []
        for i, v in enumerate(value):
            nv, f = screen(v, f"{path}/{i}")
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
