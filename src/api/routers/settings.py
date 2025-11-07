"""Settings endpoints for browser-based configuration."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.api.dependencies import get_db_session, get_settings_service
from src.api.models import (
    CredentialsSettingsRequest,
    SheetsSettingsRequest,
    ModelSettingsRequest,
    SettingsSnapshotResponse,
)
from src.services.settings_service import SettingValidationError

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])


@router.get("/", response_model=SettingsSnapshotResponse)
def read_settings(
    session: Session = Depends(get_db_session),
    settings_service=Depends(get_settings_service),
):
    return settings_service.snapshot(session)


@router.put("/credentials", response_model=SettingsSnapshotResponse)
async def update_credentials(
    request_payload: CredentialsSettingsRequest,
    session: Session = Depends(get_db_session),
    settings_service=Depends(get_settings_service),
    request: Request = None,
):
    actor = request.headers.get("x-actor") if request else None
    try:
        settings_service.update_credentials(
            session,
            path=request_payload.path,
            notes=request_payload.notes,
            actor=actor,
        )
    except SettingValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return settings_service.snapshot(session)


@router.put("/sheets", response_model=SettingsSnapshotResponse)
async def update_sheets(
    request_payload: SheetsSettingsRequest,
    session: Session = Depends(get_db_session),
    settings_service=Depends(get_settings_service),
    request: Request = None,
):
    actor = request.headers.get("x-actor") if request else None
    try:
        settings_service.update_sheets(
            session,
            spreadsheet_id=request_payload.spreadsheet_id,
            experiences_tab=request_payload.experiences_tab,
            manuals_tab=request_payload.manuals_tab,
            categories_tab=request_payload.categories_tab,
            actor=actor,
        )
    except SettingValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return settings_service.snapshot(session)


@router.put("/models", response_model=SettingsSnapshotResponse)
async def update_models(
    request_payload: ModelSettingsRequest,
    session: Session = Depends(get_db_session),
    settings_service=Depends(get_settings_service),
    request: Request = None,
):
    actor = request.headers.get("x-actor") if request else None
    settings_service.update_models(
        session,
        embedding_repo=request_payload.embedding_repo,
        embedding_quant=request_payload.embedding_quant,
        reranker_repo=request_payload.reranker_repo,
        reranker_quant=request_payload.reranker_quant,
        actor=actor,
    )
    return settings_service.snapshot(session)
