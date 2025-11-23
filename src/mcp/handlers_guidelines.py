"""MCP tool handler for guidelines retrieval.

This implementation reads generator/evaluator guidelines directly from the
local markdown files (generator.md / evaluator.md / evaluator_cpu.md)
instead of going through the API/DB. This keeps MCP behaviour stable even
when spreadsheet imports overwrite the GLN category.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from src.mcp.errors import MCPError
from src.mcp.core import config as runtime_config
from src.common.config.config import PROJECT_ROOT


def _load_markdown(path: Path) -> str:
    if not path.exists():
        raise MCPError(f"Guidelines file not found: {path}")
    return path.read_text(encoding="utf-8").strip()


def get_guidelines(guide_type: str, version: Optional[str] = None) -> Dict[str, Any]:
    """
    Return the generator or evaluator workflow manual from local markdown.

    Example:
        get_guidelines(guide_type='generator')
    """
    _ = version  # version is currently ignored; kept for API compatibility

    guide_type = guide_type.lower().strip()
    if guide_type not in {"generator", "evaluator"}:
        raise MCPError(
            f"Unknown guide type '{guide_type}'. Use 'generator' or 'evaluator'."
        )

    search_mode = getattr(runtime_config, "search_mode", "auto") if runtime_config else "auto"

    if guide_type == "generator":
        title = "Generator Workflow Guidelines"
        md_path = PROJECT_ROOT / "generator.md"
    else:  # evaluator
        if search_mode == "cpu":
            title = "Evaluator Workflow Guidelines (CPU-only)"
            md_path = PROJECT_ROOT / "evaluator_cpu.md"
        else:
            title = "Evaluator Workflow Guidelines"
            md_path = PROJECT_ROOT / "evaluator.md"

    content = _load_markdown(md_path)

    manual_id = f"GLN-{guide_type}-markdown"
    summary = title

    return {
        "meta": {
            "code": "GLN",
            "name": "chl_guidelines",
            "search_mode": search_mode,
        },
        "manual": {
            "id": manual_id,
            "title": title,
            "content": content,
            "summary": summary,
            "updated_at": None,
            "author": getattr(runtime_config, "author", None),
        },
    }


__all__ = ["get_guidelines"]
