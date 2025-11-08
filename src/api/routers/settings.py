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
    request: Request,
    request_payload: CredentialsSettingsRequest,
    session: Session = Depends(get_db_session),
    settings_service=Depends(get_settings_service),
):
    actor = request.headers.get("x-actor")
    try:
        settings_service.update_credentials(
            session,
            path=request_payload.path,
            notes=request_payload.notes,
            actor=actor,
        )
    except SettingValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Invalidate MCP categories cache so tool index reflects new settings
    try:  # Lazy import to avoid circular dependencies in some runtimes
        from src import server as mcp_server
        mcp_server.invalidate_categories_cache()
    except Exception:
        # Best effort only; MCP may run out-of-process
        pass
    return settings_service.snapshot(session)


@router.put("/sheets", response_model=SettingsSnapshotResponse)
async def update_sheets(
    request: Request,
    request_payload: SheetsSettingsRequest,
    session: Session = Depends(get_db_session),
    settings_service=Depends(get_settings_service),
):
    actor = request.headers.get("x-actor")
    try:
        settings_service.load_sheet_config(
            session,
            config_path=request_payload.config_path,
            actor=actor,
        )
    except SettingValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Invalidate MCP categories cache so tool index reflects new settings
    try:
        from src import server as mcp_server
        mcp_server.invalidate_categories_cache()
    except Exception:
        pass
    return settings_service.snapshot(session)


@router.put("/models", response_model=SettingsSnapshotResponse)
async def update_models(
    request: Request,
    request_payload: ModelSettingsRequest,
    session: Session = Depends(get_db_session),
    settings_service=Depends(get_settings_service),
):
    actor = request.headers.get("x-actor")
    settings_service.update_models(
        session,
        embedding_repo=request_payload.embedding_repo,
        embedding_quant=request_payload.embedding_quant,
        reranker_repo=request_payload.reranker_repo,
        reranker_quant=request_payload.reranker_quant,
        actor=actor,
    )
    # Invalidate MCP categories cache for completeness (models may change index hints)
    try:
        from src import server as mcp_server
        mcp_server.invalidate_categories_cache()
    except Exception:
        pass
    return settings_service.snapshot(session)
