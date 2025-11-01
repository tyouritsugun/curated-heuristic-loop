"""Pydantic models shared across MCP handlers."""
from __future__ import annotations

from typing import Any, Dict, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator


class ExperienceWritePayload(BaseModel):
    """Validated payload for creating an experience entry."""

    section: Literal["useful", "harmful", "contextual"]
    title: str = Field(..., min_length=1, max_length=120)
    playbook: str = Field(..., min_length=1, max_length=2000)
    context: Dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _check_section(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        section = data.get("section")
        valid_sections = {"useful", "harmful", "contextual"}
        if section not in valid_sections:
            raise ValueError(f"Invalid section '{section}'. Must be one of: useful, harmful, contextual")
        if section == "contextual":
            raise ValueError("Cannot write directly to 'contextual' section")
        return data

    @model_validator(mode="after")
    def _normalize_context(self) -> "ExperienceWritePayload":
        if self.section in {"useful", "harmful"}:
            # Context is ignored for useful/harmful sections.
            self.context = None
        return self


def format_validation_error(error: ValidationError) -> str:
    """Return a concise validation error message."""
    parts: list[str] = []
    for err in error.errors():
        loc = ".".join(str(piece) for piece in err.get("loc", []) if piece != "__root__")
        prefix = f"{loc}: " if loc else ""
        parts.append(f"{prefix}{err.get('msg')}")
    return "; ".join(parts)
