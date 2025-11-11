"""Worker orchestration endpoints for the web UI."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.dependencies import get_db_session, get_worker_control_service
from src.api.models import WorkerStatusResponse

router = APIRouter(prefix="/api/v1/workers", tags=["workers"])


@router.get("/", response_model=WorkerStatusResponse)
def worker_status(
    session: Session = Depends(get_db_session),
    worker_control=Depends(get_worker_control_service),
):
    return worker_control.status(session)
