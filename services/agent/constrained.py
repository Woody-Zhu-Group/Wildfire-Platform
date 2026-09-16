"""Constrained tool-call envelope used for local Qwen routing."""

from __future__ import annotations

import json
from typing import Any

from services.agent.schemas import openai_tools

_GRAMMAR_UNSAFE_KEYS = {
    "format",
    "pattern",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "description",
}


def _grammar_safe(value: Any) -> Any:
    if isinstance(value, list):
        return [_grammar_safe(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: _grammar_safe(item)
        for key, item in value.items()
        if key not in _GRAMMAR_UNSAFE_KEYS
    }


def call_envelope_schema(
    candidates: list[str],
    profile: str = "lean_enums",
) -> dict[str, Any]:
    """JSON schema for one or more tool calls drawn from the candidate set."""
    variants = []
    for tool in openai_tools(candidates, profile=profile):
        function = tool["function"]
        variants.append(
            {
                "type": "object",
                "properties": {
                    "tool": {"const": function["name"]},
                    "arguments": _grammar_safe(function["parameters"]),
                },
                "required": ["tool", "arguments"],
            }
        )
    item = variants[0] if len(variants) == 1 else {"anyOf": variants}
    return {
        "type": "object",
        "properties": {"calls": {"type": "array", "items": item}},
        "required": ["calls"],
    }


def parse_envelope_content(content: str) -> list[dict[str, Any]]:
    """Convert constrained JSON content into OpenAI-shaped tool_calls."""
    text = (content or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    entries = parsed.get("calls") if isinstance(parsed, dict) else None
    if not isinstance(entries, list):
        return []
    tool_calls: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("tool") or "")
        arguments = entry.get("arguments") or {}
        if not isinstance(arguments, dict):
            continue
        tool_calls.append(
            {
                "id": f"call_envelope_{index}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, default=str),
                },
            }
        )
    return tool_calls


ROUTING_CONSTRAINED_PROMPT = """Route this wildfire-data question with the provided tools.
Reply with JSON only, matching {"calls":[{"tool":name,"arguments":{...}}]}.
Include every call needed. Use exact schema enum values. If the question does not name
or clearly imply a specific year, omit year, start_date, and end_date. Query without a
time filter or ask for clarification; never guess a plausible year. Do not explain."""
