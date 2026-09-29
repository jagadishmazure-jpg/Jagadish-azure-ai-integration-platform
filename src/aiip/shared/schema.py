"""JSON-schema validation used for tool inputs/outputs, MCP results, events and agent cards."""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator


def errors(schema: dict[str, Any], value: Any) -> list[str]:
    v = Draft202012Validator(schema)
    return sorted(
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.validator}"
        for e in v.iter_errors(value)
    )


def check_schema(schema: dict[str, Any]) -> None:
    Draft202012Validator.check_schema(schema)
