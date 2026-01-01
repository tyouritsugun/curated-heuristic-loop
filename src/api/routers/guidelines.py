"""Guidelines endpoints for retrieving generator/evaluator workflow guides."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, Literal, Dict, Any
from pathlib import Path

from src.api.dependencies import get_db_session, get_config
from src.common.config.config import Config, PROJECT_ROOT

router = APIRouter(prefix="/api/v1/guidelines", tags=["guidelines"])

GUIDE_TITLE_MAP = {
    "generator": "Generator workflow guidelines",
    "evaluator": "Evaluator workflow guidelines",
}
GUIDE_TITLE_MAP_CPU = {
    "generator": "Generator workflow guidelines",
    "evaluator": "Evaluator workflow guidelines (CPU-only)",
}


@router.get("/{guide_type}")
def get_guidelines(
    guide_type: Literal["generator", "evaluator"],
    version: Optional[str] = Query(None, description="Optional version filter (not currently used)"),
    session: Session = Depends(get_db_session),
    config: Config = Depends(get_config)
) -> Dict[str, Any]:
    """
    Return the generator or evaluator workflow guide from the GLN category.

    In CPU mode, returns CPU-specific evaluator guidelines when guide_type='evaluator'.

    Parameters:
    - guide_type: 'generator' or 'evaluator'
    - version: Optional version filter (not currently implemented)

    Returns:
        Manual content with metadata
    """
    # Select title based on search mode
    if config.search_mode == "cpu" and guide_type == "evaluator":
        title = GUIDE_TITLE_MAP_CPU.get(guide_type)
    else:
        title = GUIDE_TITLE_MAP.get(guide_type)

    if title is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown guide type '{guide_type}'. Use 'generator' or 'evaluator'."
        )

    if guide_type == "generator":
        md_path = PROJECT_ROOT / "generator.md"
    else:
        if config.search_mode == "cpu":
            md_path = PROJECT_ROOT / "evaluator_cpu.md"
        else:
            md_path = PROJECT_ROOT / "evaluator.md"

    if not md_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Guidelines file not found: {md_path}"
        )

    content = md_path.read_text(encoding="utf-8").strip()

    return {
        "meta": {
            "code": "GLN",
            "name": "chl_guidelines",
            "search_mode": config.search_mode,
        },
        "skill": {
            "id": f"GLN-{guide_type}-markdown",
            "title": title,
            "content": content,
            "summary": title,
            "updated_at": None,
            "author": None,
        },
    }
