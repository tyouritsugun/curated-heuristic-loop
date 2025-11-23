"""Shared DTOs and validation helpers used by API.

These were migrated from `src/mcp/models.py` so that API routers no longer
depend on the MCP package. MCP talks to the API over HTTP only and should
not import these models directly.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator


class ExperienceWritePayload(BaseModel):
    """Validated payload for creating an experience entry."""

    section: Literal["useful", "harmful", "contextual"] = Field(
        ...,
        description=(
            "Entry section: 'useful' for successful patterns, "
            "'harmful' for anti-patterns, 'contextual' for context-dependent guidance"
        ),
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=120,
        description="Brief descriptive title for the experience",
    )
    playbook: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Actionable guidance or lesson learned from the experience",
    )
    context: Dict[str, Any] | None = Field(
        None,
        description="Additional context metadata (required for contextual section, ignored for useful/harmful)",
    )

    @model_validator(mode="before")
    @classmethod
    def _check_section(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        section = data.get("section")
        valid_sections = {"useful", "harmful", "contextual"}
        if section not in valid_sections:
            raise ValueError(f"Invalid section '{section}'. Must be one of: useful, harmful, contextual")
        if section == "contextual" and not data.get("context"):
            raise ValueError("Contextual entries require non-empty context metadata")
        return data

    @model_validator(mode="after")
    def _normalize_context(self) -> "ExperienceWritePayload":
        if self.section in {"useful", "harmful"}:
            self.context = None
        return self


class ManualWritePayload(BaseModel):
    """Validated payload for creating a manual entry."""

    title: str = Field(
        ...,
        min_length=1,
        max_length=120,
        description="Title of the manual (1-120 characters)",
    )
    content: str = Field(
        ...,
        min_length=1,
        description="Full markdown content of the manual",
    )
    summary: str | None = Field(
        None,
        description="Optional brief summary of the manual content",
    )


def format_validation_error(error: ValidationError) -> str:
    """Return a concise validation error message."""
    parts: list[str] = []
    for err in error.errors():
        loc = ".".join(str(piece) for piece in err.get("loc", []) if piece != "__root__")
        prefix = f"{loc}: " if loc else ""
        parts.append(f"{prefix}{err.get('msg')}")
    return "; ".join(parts)


def normalize_context(raw_context: Any) -> Any:
    """Return context data as structured JSON when stored as a serialized string."""
    if raw_context is None:
        return None
    if isinstance(raw_context, (dict, list)):
        return raw_context

    if isinstance(raw_context, str):
        stripped = raw_context.strip()
        if not stripped:
            return None
        try:
            decoded = json.loads(stripped)
            if isinstance(decoded, (dict, list)):
                return decoded
            return decoded
        except json.JSONDecodeError:
            return raw_context

    return raw_context

