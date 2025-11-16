"""CPU-specific UI router."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
)
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from src.api.dependencies import (
    get_config,
    get_db,
    get_db_session,
    get_operations_service,
    get_search_service,
    get_settings_service,
    get_telemetry_service,
    get_worker_control_service,
)
from src.api.services.settings_service import SettingValidationError, SettingsService
from src.api.services.operations_service import OperationConflict, JobNotFoundError
from src.api.services.worker_control import WorkerUnavailableError
from .ui_common import (
    templates,
    _actor_from_request,
    _build_operations_context,
    _build_settings_context,
    _operations_partial_response,
    _render_operation_result,
    _summarize_job_runs,
    _is_htmx,
)

router = APIRouter(tags=["ui"])

CPU_SETTINGS_TEMPLATE = "cpu/settings_cpu.html"
CPU_OPERATIONS_TEMPLATE = "cpu/operations_cpu.html"


def _json_serialize_datetime(obj):
    """Convert datetime objects to ISO format strings for JSON serialization."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _render_cpu_full(
    request: Request,
    session: Session,
    settings_service: SettingsService,
    *,
    message: Optional[str] = None,
    message_level: str = "info",
    error: Optional[str] = None,
):
    context = _build_settings_context(
        request,
        session,
        settings_service,
        message=message,
        message_level=message_level,
        error=error,
    )
    context["is_partial"] = False
    return templates.TemplateResponse(CPU_SETTINGS_TEMPLATE, context)


def _render_cpu_partial(
    template_name: str,
    request: Request,
    session: Session,
    settings_service: SettingsService,
    *,
    message: Optional[str] = None,
    message_level: str = "info",
    error: Optional[str] = None,
):
    context = _build_settings_context(
        request,
        session,
        settings_service,
        message=message,
        message_level=message_level,
        error=error,
    )
    context["is_partial"] = True
    return templates.TemplateResponse(template_name, context)


def _cpu_respond(
    template_name: Optional[str],
    request: Request,
    session: Session,
    settings_service: SettingsService,
    *,
    message: Optional[str] = None,
    message_level: str = "info",
    error: Optional[str] = None,
    trigger_event: Optional[str] = None,
):
    if template_name and _is_htmx(request):
        response = _render_cpu_partial(
            template_name,
            request,
            session,
            settings_service,
            message=message,
            message_level=message_level,
            error=error,
        )
    else:
        response = _render_cpu_full(
            request,
            session,
            settings_service,
            message=message,
            message_level=message_level,
            error=error,
        )
    if trigger_event and hasattr(response, "headers"):
        response.headers.setdefault("HX-Trigger", trigger_event)
    return response


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    operations_service=Depends(get_operations_service),
):
    context = _build_settings_context(request, session, settings_service)
    jobs = operations_service.list_recent(session, limit=10)
    job_summaries = _summarize_job_runs(jobs, operations_service.last_runs_by_type(session))
    context["job_summaries"] = job_summaries
    context["is_partial"] = False
    context["search_mode"] = "cpu"
    return templates.TemplateResponse(CPU_SETTINGS_TEMPLATE, context)


@router.get("/ui/settings/config-status", response_class=HTMLResponse)
def settings_config_status_card(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    telemetry_service=Depends(get_telemetry_service),
    operations_service=Depends(get_operations_service),
    worker_control=Depends(get_worker_control_service),
):
    return _render_operation_result(
        "common/partials/config_status_card.html",
        request,
        session,
        settings_service,
        telemetry_service,
        operations_service,
        worker_control,
    )


@router.get("/ui/stream/telemetry")
async def telemetry_stream(
    request: Request,
    settings_service: SettingsService = Depends(get_settings_service),
    telemetry_service=Depends(get_telemetry_service),
    operations_service=Depends(get_operations_service),
    worker_control=Depends(get_worker_control_service),
    search_service=Depends(get_search_service),
    config=Depends(get_config),
    db=Depends(get_db),
):
    session_factory = db.get_session

    try:
        cycles_param = request.query_params.get("cycles")
        max_cycles = int(cycles_param) if cycles_param is not None else None
    except ValueError:
        max_cycles = None

    try:
        interval_param = request.query_params.get("interval")
        interval_seconds = float(interval_param) if interval_param is not None else 2.0
    except ValueError:
        interval_seconds = 2.0
    interval_seconds = max(0.5, interval_seconds)

    async def event_generator():
        cycles = 0
        while True:
            if await request.is_disconnected():
                break

            session = session_factory()
            try:
                context = _build_operations_context(
                    session,
                    settings_service,
                    telemetry_service,
                    operations_service,
                    worker_control,
                    search_service,
                    config,
                )
            finally:
                session.close()

            snapshot = context.get("snapshot") or {}
            queue_payload = snapshot.get("queue") or {}
            index_payload = context.get("index_info") or {}
            controls_payload = {
                "worker_status": context.get("worker_status"),
                "job_summaries": context.get("job_summaries"),
                "jobs": context.get("jobs"),
            }

            yield f"event: queue\ndata: {json.dumps(queue_payload, ensure_ascii=False, default=_json_serialize_datetime)}\n\n"
            yield f"event: index\ndata: {json.dumps(index_payload or {}, ensure_ascii=False, default=_json_serialize_datetime)}\n\n"
            yield f"event: controls\ndata: {json.dumps(controls_payload, ensure_ascii=False, default=_json_serialize_datetime)}\n\n"

            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            await asyncio.sleep(interval_seconds)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/operations", response_class=HTMLResponse)
def operations_page(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    telemetry_service=Depends(get_telemetry_service),
    operations_service=Depends(get_operations_service),
    worker_control=Depends(get_worker_control_service),
    search_service=Depends(get_search_service),
    config=Depends(get_config),
):
    context = _build_operations_context(
        session,
        settings_service,
        telemetry_service,
        operations_service,
        worker_control,
        search_service,
        config,
    )
    context.update(
        {
            "request": request,
            "message": None,
            "message_level": "info",
            "error": None,
            "is_partial": False,
            "search_mode": "cpu",
        }
    )
    return templates.TemplateResponse(CPU_OPERATIONS_TEMPLATE, context)


@router.get("/ui/operations/queue", response_class=HTMLResponse)
def operations_queue_card(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    telemetry_service=Depends(get_telemetry_service),
    operations_service=Depends(get_operations_service),
    worker_control=Depends(get_worker_control_service),
):
    return _operations_partial_response(
        "common/partials/ops_queue_card.html",
        request,
        session,
        settings_service,
        telemetry_service,
        operations_service,
        worker_control,
    )


@router.get("/ui/operations/jobs", response_class=HTMLResponse)
def operations_jobs_card(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    telemetry_service=Depends(get_telemetry_service),
    operations_service=Depends(get_operations_service),
    worker_control=Depends(get_worker_control_service),
):
    return _operations_partial_response(
        "common/partials/ops_jobs_card.html",
        request,
        session,
        settings_service,
        telemetry_service,
        operations_service,
        worker_control,
    )


@router.get("/ui/operations/controls", response_class=HTMLResponse)
def operations_controls_card(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    telemetry_service=Depends(get_telemetry_service),
    operations_service=Depends(get_operations_service),
    worker_control=Depends(get_worker_control_service),
):
    return _operations_partial_response(
        "common/partials/ops_operations_card.html",
        request,
        session,
        settings_service,
        telemetry_service,
        operations_service,
        worker_control,
    )


@router.post("/ui/operations/run/{operation_type}", response_class=HTMLResponse)
async def run_operation_from_ui(
    request: Request,
    operation_type: str,
    payload: Optional[str] = Form(None),
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    telemetry_service=Depends(get_telemetry_service),
    operations_service=Depends(get_operations_service),
    worker_control=Depends(get_worker_control_service),
):
    actor = _actor_from_request(request)
    parsed_payload = None
    if payload:
        try:
            parsed_payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            target_template = (
                "common/partials/config_status_card.html"
                if operation_type in ("import", "export")
                else "common/partials/ops_operations_card.html"
            )
            return _render_operation_result(
                target_template,
                request,
                session,
                settings_service,
                telemetry_service,
                operations_service,
                worker_control,
                error=f"Invalid payload JSON: {exc.msg}",
            )

    def _target_for(op: str) -> str:
        return (
            "common/partials/config_status_card.html"
            if op in {"import", "export", "guidelines"}
            else "common/partials/ops_operations_card.html"
        )

    try:
        operations_service.trigger(operation_type, parsed_payload, actor)
    except OperationConflict as exc:
        target_template = _target_for(operation_type)
        return _render_operation_result(
            target_template,
            request,
            session,
            settings_service,
            telemetry_service,
            operations_service,
            worker_control,
            error=str(exc),
        )
    except ValueError as exc:
        target_template = _target_for(operation_type)
        return _render_operation_result(
            target_template,
            request,
            session,
            settings_service,
            telemetry_service,
            operations_service,
            worker_control,
            error=str(exc),
        )

    target_template = _target_for(operation_type)
    return _render_operation_result(
        target_template,
        request,
        session,
        settings_service,
        telemetry_service,
        operations_service,
        worker_control,
        message=f"{operation_type.title()} job queued",
        message_level="success",
        trigger_event="operations-updated",
    )


@router.post("/ui/operations/jobs/{job_id}/cancel", response_class=HTMLResponse)
async def cancel_job(
    request: Request,
    job_id: int,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    telemetry_service=Depends(get_telemetry_service),
    operations_service=Depends(get_operations_service),
    worker_control=Depends(get_worker_control_service),
):
    actor = _actor_from_request(request)
    try:
        operations_service.cancel(job_id, actor=actor)
    except JobNotFoundError as exc:
        return _render_operation_result(
            "common/partials/ops_operations_card.html",
            request,
            session,
            settings_service,
            telemetry_service,
            operations_service,
            worker_control,
            error=str(exc),
        )

    return _render_operation_result(
        "common/partials/ops_operations_card.html",
        request,
        session,
        settings_service,
        telemetry_service,
        operations_service,
        worker_control,
        message=f"Job {job_id} cancelled.",
        message_level="success",
        trigger_event="operations-updated",
    )


@router.post("/ui/workers/{action}", response_class=HTMLResponse)
async def control_workers(
    action: str,
    request: Request,
    worker_control=Depends(get_worker_control_service),
):
    actor = _actor_from_request(request)
    try:
        if action == "pause":
            worker_control.pause(actor=actor)
            message = "Workers paused."
        elif action == "resume":
            worker_control.resume(actor=actor)
            message = "Workers resumed."
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported worker action '{action}'")
    except WorkerUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return JSONResponse(
        content={
            "success": True,
            "message": message,
            "action": action,
        }
    )


@router.post("/ui/settings/sheets", response_class=HTMLResponse)
async def load_sheets_config(
    request: Request,
    config_path: str = Form(...),
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    return _cpu_respond(
        None,
        request,
        session,
        settings_service,
        error="Sheet configuration is now managed via .env file. Please edit .env and restart if needed.",
    )


@router.post("/ui/settings/test-connection", response_class=HTMLResponse)
async def test_connection(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    credentials_path = os.getenv("GOOGLE_CREDENTIAL_PATH", "")
    if not credentials_path:
        return _cpu_respond(
            "common/partials/config_status_card.html",
            request,
            session,
            settings_service,
            error="GOOGLE_CREDENTIAL_PATH not set in .env file",
        )

    cred_file = Path(credentials_path)
    if not cred_file.is_absolute():
        project_root = Path(__file__).resolve().parents[3]
        cred_file = (project_root / cred_file).resolve()

    if not cred_file.exists():
        return _cpu_respond(
            "common/partials/config_status_card.html",
            request,
            session,
            settings_service,
            error=f"Credential file not found: {cred_file}",
        )

    try:
        from src.common.storage.sheets_client import SheetsClient

        SheetsClient(str(cred_file))
        return _cpu_respond(
            "common/partials/config_status_card.html",
            request,
            session,
            settings_service,
            message="Connection test successful. Credentials are valid.",
            message_level="success",
        )
    except Exception as exc:
        return _cpu_respond(
            "common/partials/config_status_card.html",
            request,
            session,
            settings_service,
            error=f"Connection test failed: {str(exc)}",
        )


@router.get("/ui/settings/diagnostics", response_class=HTMLResponse)
def diagnostics_panel(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    return _render_cpu_partial("common/partials/diagnostics_panel.html", request, session, settings_service)


@router.post("/ui/settings/diagnostics", response_class=HTMLResponse)
async def diagnostics_probe(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    return _cpu_respond(
        "common/partials/diagnostics_panel.html",
        request,
        session,
        settings_service,
        message="Diagnostics refreshed. Configuration is managed via .env file.",
        message_level="success",
        trigger_event="settings-changed",
    )


@router.get("/ui/settings/backup")
def download_settings_backup(
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    snapshot = settings_service.snapshot(session)
    response = JSONResponse(content=snapshot)
    response.headers["Content-Disposition"] = "attachment; filename=chl-settings-backup.json"
    return response


@router.get("/ui/settings/audit-log", response_class=HTMLResponse)
def audit_log_panel(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    return _render_cpu_partial("common/partials/audit_log.html", request, session, settings_service)


@router.post("/ui/settings/backup/restore", response_class=HTMLResponse)
async def restore_settings_backup(
    request: Request,
    backup_json: str = Form(...),
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    actor = _actor_from_request(request)
    backup_json = (backup_json or "").strip()
    if not backup_json:
        return _cpu_respond(
            "common/partials/backup_card.html",
            request,
            session,
            settings_service,
            error="Backup JSON payload is required.",
        )

    try:
        payload = json.loads(backup_json)
    except json.JSONDecodeError as exc:
        return _cpu_respond(
            "common/partials/backup_card.html",
            request,
            session,
            settings_service,
            error=f"Invalid JSON: {exc.msg}.",
        )

    if not isinstance(payload, dict):
        return _cpu_respond(
            "common/partials/backup_card.html",
            request,
            session,
            settings_service,
            error="Backup payload must be a JSON object.",
        )

    applied: list[str] = []
    credentials = payload.get("credentials")
    if isinstance(credentials, dict) and credentials.get("path"):
        try:
            settings_service.update_credentials(
                session,
                path=credentials["path"],
                notes=credentials.get("notes"),
                actor=actor,
            )
            applied.append("credentials")
        except SettingValidationError as exc:
            return _cpu_respond(
                "common/partials/backup_card.html",
                request,
                session,
                settings_service,
                error=f"Credential restore failed: {exc}",
            )

    sheets = payload.get("sheets")
    if isinstance(sheets, dict):
        config_path = sheets.get("config_path")
        if config_path:
            try:
                settings_service.load_sheet_config(
                    session,
                    config_path=config_path,
                    actor=actor,
                )
                applied.append("sheets")
            except SettingValidationError as exc:
                return _cpu_respond(
                    "common/partials/backup_card.html",
                    request,
                    session,
                    settings_service,
                    error=f"Sheet config restore failed: {exc}",
                )
        elif any(key in sheets for key in ("spreadsheet_id", "experiences_tab", "manuals_tab", "categories_tab")):
            return _cpu_respond(
                "common/partials/backup_card.html",
                request,
                session,
                settings_service,
                error="Backup was created before scripts_config.yaml support. Reload the YAML via Settings instead.",
            )

    models = payload.get("models")
    if isinstance(models, dict):
        settings_service.update_models(
            session,
            embedding_repo=models.get("embedding_repo"),
            embedding_quant=models.get("embedding_quant"),
            reranker_repo=models.get("reranker_repo"),
            reranker_quant=models.get("reranker_quant"),
            actor=actor,
        )
        applied.append("models")

    if not applied:
        return _cpu_respond(
            "common/partials/backup_card.html",
            request,
            session,
            settings_service,
            error="No restorable sections found in backup payload.",
        )

    applied_msg = ", ".join(applied)
    return _cpu_respond(
        "common/partials/backup_card.html",
        request,
        session,
        settings_service,
        message=f"Restored sections: {applied_msg}.",
        message_level="success",
        trigger_event="settings-changed",
    )
