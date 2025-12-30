"""Guidelines endpoints for retrieving generator/evaluator workflow guides."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, Literal, Dict, Any

from src.api.dependencies import get_db_session, get_config
from src.common.storage.repository import CategoryManualRepository, CategoryRepository
from src.common.config.config import Config

router = APIRouter(prefix="/api/v1/guidelines", tags=["guidelines"])

GUIDELINES_CATEGORY = "GLN"
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

    cat_repo = CategoryRepository(session)
    manual_repo = CategoryManualRepository(session)

    category = cat_repo.get_by_code(GUIDELINES_CATEGORY)
    if not category:
        raise HTTPException(
            status_code=404,
            detail="Guidelines category not found. Run 'uv run python scripts/seed_default_content.py' to seed it."
        )

    manuals = manual_repo.get_by_category(GUIDELINES_CATEGORY)
    manual = next((m for m in manuals if m.title == title), None)
    if not manual:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Guideline '{title}' not found. Update generator.md/evaluator.md/evaluator_cpu.md and run "
                "'uv run python scripts/seed_default_content.py'."
            )
        )

    return {
        "meta": {
            "code": category.code,
            "name": category.name,
            "search_mode": config.search_mode,
        },
        "manual": {
            "id": manual.id,
            "title": manual.title,
            "content": manual.content,
            "summary": manual.summary,
            "updated_at": manual.updated_at,
            "author": manual.author,
        },
    }
