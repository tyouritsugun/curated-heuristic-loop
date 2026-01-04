"""Normalization helpers for skill import/export."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List


def normalize_allowed_tools(value: Any | None) -> List[str]:
    """Normalize allowed-tools to a list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if raw.startswith("[") and raw.endswith("]"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except json.JSONDecodeError:
                pass
        parts = raw.split(",") if "," in raw else raw.split()
        return [part.strip() for part in parts if part.strip()]
    return [str(value).strip()]


def format_allowed_tools(allowed_tools: Iterable[str], delimiter: str) -> str | None:
    tools = [str(item).strip() for item in allowed_tools if str(item).strip()]
    if not tools:
        return None
    if delimiter == "comma":
        return ", ".join(tools)
    return " ".join(tools)


def flatten_metadata(value: Any | None) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        flattened: Dict[str, Any] = {}

        def _flatten(obj: dict, prefix: str = "") -> None:
            for key, item in obj.items():
                key_name = f"{prefix}.{key}" if prefix else str(key)
                if isinstance(item, dict):
                    _flatten(item, key_name)
                else:
                    flattened[key_name] = item

        _flatten(value)
        return flattened
    return {"value": value}


def parse_metadata_json(raw: str | None) -> Dict[str, Any]:
    if raw is None:
        return {}
    if not isinstance(raw, str):
        return {"value": raw}
    stripped = raw.strip()
    if not stripped:
        return {}
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return {"raw": stripped}
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}
