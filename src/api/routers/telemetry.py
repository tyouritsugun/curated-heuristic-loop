"""Telemetry snapshot endpoints."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.api.dependencies import get_db_session, get_telemetry_service
from src.api.models import TelemetrySnapshotResponse

router = APIRouter(prefix="/api/v1/telemetry", tags=["telemetry"])


@router.get("/snapshot", response_model=TelemetrySnapshotResponse)
def telemetry_snapshot(
    session: Session = Depends(get_db_session),
    telemetry_service=Depends(get_telemetry_service),
):
    return telemetry_service.snapshot(session)
