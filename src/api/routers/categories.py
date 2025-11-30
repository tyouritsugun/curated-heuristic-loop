"""Category endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from src.api.dependencies import get_db_session
from src.api.models import ListCategoriesResponse, CategoryResponse
from src.common.storage.repository import CategoryRepository, ExperienceRepository, CategoryManualRepository

router = APIRouter(prefix="/api/v1/categories", tags=["categories"])


@router.get("/", response_model=ListCategoriesResponse)
def list_categories(session: Session = Depends(get_db_session)):
    """List all available category shelves with entry counts."""
    cat_repo = CategoryRepository(session)
    exp_repo = ExperienceRepository(session)
    man_repo = CategoryManualRepository(session)

    categories = cat_repo.get_all()

    return ListCategoriesResponse(
        categories=[
            CategoryResponse(
                code=cat.code,
                name=cat.name,
                description=cat.description,
                created_at=cat.created_at.isoformat() if cat.created_at else None,
                experience_count=len(exp_repo.get_by_category(cat.code)),
                manual_count=len(man_repo.get_by_category(cat.code)),
                total_count=len(exp_repo.get_by_category(cat.code)) + len(man_repo.get_by_category(cat.code)),
            )
            for cat in categories
        ]
    )
