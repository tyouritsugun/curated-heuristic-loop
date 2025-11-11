"""Settings endpoints for browser-based configuration."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.dependencies import get_db_session, get_settings_service
from src.api.models import SettingsSnapshotResponse

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


@router.get("/", response_model=SettingsSnapshotResponse)
def read_settings(
    session: Session = Depends(get_db_session),
    settings_service=Depends(get_settings_service),
):
    return settings_service.snapshot(session)
