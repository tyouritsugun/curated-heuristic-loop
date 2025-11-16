"""Category endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from src.api.dependencies import get_db_session
from src.api.models import ListCategoriesResponse, CategoryResponse
from src.common.storage.repository import CategoryRepository

router = APIRouter(prefix="/api/v1/categories", tags=["categories"])


@router.get("/", response_model=ListCategoriesResponse)
def list_categories(session: Session = Depends(get_db_session)):
    """List all available category shelves."""
    repo = CategoryRepository(session)
    categories = repo.get_all()

    return ListCategoriesResponse(
        categories=[
            CategoryResponse(
                code=cat.code,
                name=cat.name,
                description=cat.description,
                created_at=cat.created_at,
            )
            for cat in categories
        ]
    )
