"""Guidelines endpoints for retrieving generator/evaluator workflow manuals."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Optional, Literal, Dict, Any

from src.api.dependencies import get_db_session
from src.storage.repository import CategoryManualRepository, CategoryRepository

router = APIRouter(prefix="/api/v1/guidelines", tags=["guidelines"])

GUIDELINES_CATEGORY = "GLN"
GUIDE_TITLE_MAP = {
    "generator": "Generator workflow guidelines",
    "evaluator": "Evaluator workflow guidelines",
}


@router.get("/{guide_type}")
def get_guidelines(
    guide_type: Literal["generator", "evaluator"],
    version: Optional[str] = Query(None, description="Optional version filter (not currently used)"),
    session: Session = Depends(get_db_session)
) -> Dict[str, Any]:
    """
    Return the generator or evaluator workflow manual from the GLN category.

    Parameters:
    - guide_type: 'generator' or 'evaluator'
    - version: Optional version filter (not currently implemented)

    Returns:
        Manual content with metadata
    """
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
                "Guideline manual not found. Update generator.md/evaluator.md and run "
                "'uv run python scripts/seed_default_content.py'."
            )
        )

    return {
        "meta": {
            "code": category.code,
            "name": category.name,
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
