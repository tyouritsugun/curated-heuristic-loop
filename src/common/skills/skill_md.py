"""Parse and emit SKILL.md files (YAML frontmatter + Markdown body)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .normalize import flatten_metadata, format_allowed_tools, normalize_allowed_tools, parse_metadata_json


FRONTMATTER_DELIM = "---"
NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")


def _split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_DELIM:
        raise ValueError("Missing YAML frontmatter (expected '---' on first line)")
    for i in range(1, len(lines)):
        if lines[i].strip() == FRONTMATTER_DELIM:
            yaml_block = "\n".join(lines[1:i])
            body = "\n".join(lines[i + 1 :]).lstrip("\n")
            return yaml_block, body
    raise ValueError("Missing closing '---' for YAML frontmatter")


def _require_non_empty(value: Any, field: str) -> str:
    if value is None:
        raise ValueError(f"Missing required field: {field}")
    text = str(value).strip()
    if not text:
        raise ValueError(f"Missing required field: {field}")
    return text


def _validate_name(name: str) -> None:
    if not (1 <= len(name) <= 64):
        raise ValueError("Skill name must be 1-64 characters")
    if name.lower() != name:
        raise ValueError("Skill name must be lowercase")
    if "--" in name or not NAME_PATTERN.match(name):
        raise ValueError("Skill name must be kebab-case (lowercase letters, digits, hyphens)")


def parse_skill_md(
    path: Path,
    *,
    require_dir_match: bool = True,
    default_category_code: str | None = None,
) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    yaml_text, content = _split_frontmatter(text)
    data = yaml.safe_load(yaml_text) or {}
    if not isinstance(data, dict):
        raise ValueError("YAML frontmatter must be a mapping")

    name = _require_non_empty(data.get("name"), "name")
    description = _require_non_empty(data.get("description"), "description")
    content = _require_non_empty(content, "content")

    _validate_name(name)
    if not (1 <= len(description) <= 1024):
        raise ValueError("Skill description must be 1-1024 characters")

    if require_dir_match and path.parent.name != name:
        raise ValueError(f"Directory name '{path.parent.name}' must match skill name '{name}'")

    allowed_raw = data.get("allowed-tools") or data.get("allowed_tools")
    allowed_tools = normalize_allowed_tools(allowed_raw)

    metadata_raw = data.get("metadata")
    metadata_value = metadata_raw if isinstance(metadata_raw, dict) else {"value": metadata_raw} if metadata_raw is not None else None
    metadata = flatten_metadata(metadata_value)

    category_code = None
    if "chl.category_code" in metadata:
        category_code = str(metadata.get("chl.category_code")).strip().upper()

    if not category_code:
        category_code = default_category_code.upper() if default_category_code else None

    if not category_code:
        raise ValueError("Missing category_code (expected metadata.chl.category_code or default_category_code)")

    payload: Dict[str, Any] = {
        "name": name,
        "description": description,
        "content": content,
        "license": data.get("license"),
        "compatibility": data.get("compatibility"),
        "allowed_tools": allowed_tools,
        "model": data.get("model"),
        "metadata": metadata if metadata else None,
        "category_code": category_code,
    }
    return payload


def build_skill_md(
    skill: Any,
    *,
    allowed_tools_delimiter: str = "comma",
) -> str:
    metadata = parse_metadata_json(getattr(skill, "metadata_json", None))
    category_code = getattr(skill, "category_code", None)
    if category_code:
        metadata.setdefault("chl.category_code", category_code)

    frontmatter: Dict[str, Any] = {
        "name": getattr(skill, "name", None),
        "description": getattr(skill, "description", None),
    }

    license_value = getattr(skill, "license", None)
    if license_value:
        frontmatter["license"] = license_value

    compatibility = getattr(skill, "compatibility", None)
    if compatibility:
        frontmatter["compatibility"] = compatibility

    allowed_tools_raw = getattr(skill, "allowed_tools", None)
    allowed_tools = normalize_allowed_tools(allowed_tools_raw)
    allowed_tools_value = format_allowed_tools(allowed_tools, allowed_tools_delimiter)
    if allowed_tools_value:
        frontmatter["allowed-tools"] = allowed_tools_value

    model_value = getattr(skill, "model", None)
    if model_value:
        frontmatter["model"] = model_value

    if metadata:
        frontmatter["metadata"] = metadata

    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    body = getattr(skill, "content", "") or ""
    return f"{FRONTMATTER_DELIM}\n{yaml_text}\n{FRONTMATTER_DELIM}\n\n{body.rstrip()}\n"
