"""Worker orchestration endpoints for the web UI."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from src.api.dependencies import get_db_session, get_worker_control_service
from src.api.models import WorkerStatusResponse, WorkerActionResponse
from src.services.worker_control import WorkerUnavailableError

router = APIRouter(prefix="/api/v1/workers", tags=["workers"])


@router.get("/", response_model=WorkerStatusResponse)
def worker_status(
    session: Session = Depends(get_db_session),
    worker_control=Depends(get_worker_control_service),
):
    return worker_control.status(session)


@router.post("/pause", response_model=WorkerActionResponse)
async def pause_workers(
    request: Request,
    session: Session = Depends(get_db_session),
    worker_control=Depends(get_worker_control_service),
):
    actor = request.headers.get("x-actor")
    try:
        return worker_control.pause(session, actor)
    except WorkerUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/resume", response_model=WorkerActionResponse)
async def resume_workers(
    request: Request,
    session: Session = Depends(get_db_session),
    worker_control=Depends(get_worker_control_service),
):
    actor = request.headers.get("x-actor")
    try:
        return worker_control.resume(session, actor)
    except WorkerUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/drain", response_model=WorkerActionResponse)
async def drain_workers(
    request: Request,
    timeout: int = Query(300, ge=10, le=3600),
    session: Session = Depends(get_db_session),
    worker_control=Depends(get_worker_control_service),
):
    actor = request.headers.get("x-actor")
    try:
        result = worker_control.drain(session, timeout, actor)
    except WorkerUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return WorkerActionResponse(**result)
