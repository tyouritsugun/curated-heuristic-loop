"""Operation orchestration endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.api.dependencies import get_db_session, get_operations_service
from src.api.models import OperationRequest, OperationResponse, JobStatusResponse
from src.services.operations_service import OperationConflict, JobNotFoundError

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])


@router.post("/{operation_type}", response_model=OperationResponse)
async def trigger_operation(
    request: Request,
    operation_type: str,
    request_payload: OperationRequest | None = None,
    operations_service=Depends(get_operations_service),
):
    actor = request.headers.get("x-actor")
    payload = request_payload.payload if request_payload else None
    try:
        return operations_service.trigger(operation_type, payload, actor)
    except OperationConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def job_status(
    job_id: str,
    session: Session = Depends(get_db_session),
    operations_service=Depends(get_operations_service),
):
    try:
        return operations_service.get_job(job_id, session)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/jobs/{job_id}/cancel", response_model=JobStatusResponse)
async def cancel_job(
    request: Request,
    job_id: str,
    operations_service=Depends(get_operations_service),
):
    actor = request.headers.get("x-actor")
    try:
        return operations_service.cancel_job(job_id, actor)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
