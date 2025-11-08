"""Server-rendered UI endpoints for settings and operations management."""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from src.api.dependencies import (
    get_config,
    get_db_session,
    get_operations_service,
    get_search_service,
    get_settings_service,
    get_telemetry_service,
    get_worker_control_service,
)
from src.services.settings_service import SettingValidationError, SettingsService
from src.services.operations_service import OperationConflict, JobNotFoundError
from src.services.worker_control import WorkerUnavailableError
from src.storage.repository import AuditLogRepository
from src.storage.schema import AuditLog, utc_now


TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "web" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

MAX_UPLOAD_BYTES = 512 * 1024  # 512 KiB upper bound for credential JSON
MAX_INDEX_ARCHIVE_BYTES = 512 * 1024 * 1024  # 512 MiB cap for FAISS snapshots
WEB_ACTOR = "web-ui"
ALLOWED_INDEX_FILE_SUFFIXES = frozenset([".index", ".json", ".backup"])  # case-insensitive

logger = logging.getLogger(__name__)


class CredentialUploadError(Exception):
    """Raised when a credential upload fails validation."""


class IndexUploadError(Exception):
    """Raised when an index snapshot upload fails validation."""


router = APIRouter(tags=["ui"])


def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request", "").lower() == "true"


def _invalidate_categories_cache_safe() -> None:
    """Best-effort MCP categories cache invalidation (may run out-of-process)."""
    try:
        from src import server as mcp_server
        mcp_server.invalidate_categories_cache()
    except Exception:
        # MCP server may not be in-process; ignore
        pass


def _recent_audit_entries(session: Session, limit: int = 8):
    repo = AuditLogRepository(session)
    entries = []
    for row in repo.list_recent(limit):
        context = None
        if row.context:
            try:
                context = json.loads(row.context)
            except json.JSONDecodeError:
                context = row.context
        if context is None:
            pretty = None
        elif isinstance(context, str):
            pretty = context
        else:
            pretty = json.dumps(context, ensure_ascii=False, indent=2)
        entries.append(
            {
                "id": row.id,
                "event_type": row.event_type,
                "actor": row.actor or "n/a",
                "context": context,
                "context_pretty": pretty,
                "created_at": row.created_at,
            }
        )
    return entries


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _format_timestamp(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.astimezone(timezone.utc).strftime("%b %d %H:%M UTC")


def _format_duration(start: Optional[datetime], end: Optional[datetime]) -> Optional[str]:
    if not start:
        return None
    end = end or datetime.now(timezone.utc)
    total_seconds = max((end - start).total_seconds(), 0)
    if total_seconds < 1:
        return "<1s"
    minutes, seconds = divmod(int(total_seconds), 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def _format_size(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "0 B"
    step = 1024.0
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    idx = 0
    value = float(num_bytes)
    while value >= step and idx < len(units) - 1:
        value /= step
        idx += 1
    return f"{value:.1f} {units[idx]}"


def _job_status_class(status: str) -> str:
    mapping = {
        "succeeded": "ok",
        "running": "info",
        "queued": "warn",
        "failed": "error",
        "cancelled": "warn",
    }
    return mapping.get(status, "info")


def _summarize_job_runs(jobs, last_runs):
    summaries = {}
    observed_types = {job.get("job_type") for job in jobs if job.get("job_type")}
    observed_types.update(last_runs.keys())
    for job_type in sorted(observed_types or {"import", "export", "index"}):
        active = next(
            (
                job
                for job in jobs
                if job.get("job_type") == job_type and job.get("status") in {"running", "queued"}
            ),
            None,
        )
        record = active or last_runs.get(job_type)
        if not record:
            summaries[job_type] = None
            continue
        status = record.get("status", "unknown")
        started_at = _parse_iso(record.get("started_at") or record.get("created_at"))
        completed_at = _parse_iso(record.get("finished_at") or record.get("cancelled_at"))
        reference_ts = completed_at or started_at
        description_parts = []
        timestamp_label = _format_timestamp(reference_ts)
        if timestamp_label:
            description_parts.append(timestamp_label)
        duration_label = _format_duration(started_at, completed_at)
        if duration_label:
            description_parts.append(duration_label)
        actor = record.get("requested_by")
        if actor:
            description_parts.append(f"by {actor}")
        if record.get("error") and status in {"failed", "cancelled"}:
            description_parts.append(record["error"][:80])

        summaries[job_type] = {
            "job_id": record.get("job_id"),
            "status": status,
            "status_label": status.replace("_", " ").title(),
            "pill_class": _job_status_class(status),
            "description": " · ".join(description_parts) if description_parts else "",
            "is_active": status in {"running", "queued"},
        }
    return summaries


def _index_dir(config) -> Optional[Path]:
    if not config:
        return None
    path = Path(getattr(config, "faiss_index_path", ""))
    return path if str(path) else None


def _index_meta(index_dir: Optional[Path]):
    if not index_dir or not index_dir.exists():
        return None
    for meta_file in sorted(index_dir.glob("*.meta.json")):
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
    return None


def _index_files(index_dir: Optional[Path]):
    if not index_dir or not index_dir.exists():
        return []
    files = []
    for path in sorted(p for p in index_dir.iterdir() if p.is_file()):
        try:
            stat = path.stat()
        except OSError:
            continue
        modified = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
        files.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "size_label": _format_size(stat.st_size),
                "modified_at": modified.isoformat(),
                "modified_label": _format_timestamp(modified),
            }
        )
    return files


def _build_index_info(search_service, config):
    index_dir = _index_dir(config)
    info = {
        "directory": str(index_dir) if index_dir else None,
        "files": _index_files(index_dir),
        "meta": _index_meta(index_dir),
        "state": "unknown",
        "message": None,
        "vector_count": None,
        "model_name": None,
        "dimension": None,
        "tombstone_ratio": None,
        "needs_rebuild": False,
    }

    if not search_service:
        info["state"] = "warn"
        info["message"] = "Search service unavailable"
        return info

    try:
        vector_provider = search_service.get_vector_provider()
    except Exception as exc:  # pragma: no cover - defensive
        info["state"] = "error"
        info["message"] = f"Search provider error: {exc}"
        return info

    if not vector_provider or not getattr(vector_provider, "is_available", False):
        info["state"] = "warn"
        info["message"] = "Vector provider disabled"
        return info

    faiss_manager = getattr(vector_provider, "index_manager", None)
    if not faiss_manager:
        info["state"] = "warn"
        info["message"] = "Index manager unavailable"
        return info

    try:
        underlying = getattr(faiss_manager, "_manager", faiss_manager)
        index_obj = getattr(underlying, "index", None)
        vector_count = getattr(index_obj, "ntotal", None)
        info.update(
            {
                "state": "ok",
                "vector_count": vector_count,
                "model_name": getattr(underlying, "model_name", None),
                "dimension": getattr(underlying, "dimension", None),
                "tombstone_ratio": getattr(faiss_manager, "get_tombstone_ratio", lambda: None)(),
                "needs_rebuild": bool(getattr(faiss_manager, "needs_rebuild", lambda: False)()),
            }
        )
    except Exception as exc:  # pragma: no cover - defensive
        info["state"] = "error"
        info["message"] = f"Failed to inspect index: {exc}"

    return info


def _build_settings_context(
    request: Request,
    session: Session,
    settings_service: SettingsService,
    *,
    message: Optional[str] = None,
    message_level: str = "info",
    error: Optional[str] = None,
):
    snapshot = settings_service.snapshot(session)
    diagnostics = settings_service.diagnostics(session)
    context = {
        "request": request,
        "snapshot": snapshot,
        "message": message,
        "message_level": message_level,
        "error": error,
        "managed_credentials_dir": str(_managed_credentials_dir(settings_service)),
        "secrets_root": str(settings_service.secrets_root),
        "diagnostics": {name: status.to_dict() for name, status in diagnostics.items()},
        "audit_entries": _recent_audit_entries(session),
        "active_page": "settings",
    }
    return context


def _render_full(
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
    return templates.TemplateResponse(request, "settings.html", context)


def _render_partial(
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
    return templates.TemplateResponse(request, template_name, context)


def _respond(
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
        response = _render_partial(
            template_name,
            request,
            session,
            settings_service,
            message=message,
            message_level=message_level,
            error=error,
        )
    else:
        response = _render_full(
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


def _build_operations_context(
    session: Session,
    settings_service: SettingsService,
    telemetry_service,
    operations_service,
    worker_control,
    search_service=None,
    config=None,
    *,
    jobs_limit: int = 10,
):
    diagnostics = settings_service.diagnostics(session)
    telemetry_snapshot = telemetry_service.snapshot(session)
    worker_status = worker_control.status(session)
    jobs = operations_service.list_recent(session, limit=jobs_limit)
    job_summaries = _summarize_job_runs(jobs, operations_service.last_runs_by_type(session))
    index_info = _build_index_info(search_service, config) if (config or search_service) else None
    return {
        "snapshot": telemetry_snapshot,
        "worker_status": worker_status,
        "jobs": jobs,
        "diagnostics": {name: status.to_dict() for name, status in diagnostics.items()},
        "job_summaries": job_summaries,
        "index_info": index_info,
        "active_page": "operations",
    }


def _render_operations_template(
    template_name: str,
    request: Request,
    session: Session,
    settings_service: SettingsService,
    telemetry_service,
    operations_service,
    worker_control,
    search_service=None,
    config=None,
    *,
    message: Optional[str] = None,
    message_level: str = "info",
    error: Optional[str] = None,
    is_partial: bool = True,
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
            "message": message,
            "message_level": message_level,
            "error": error,
            "is_partial": is_partial,
        }
    )
    return templates.TemplateResponse(request, template_name, context)


def _operations_partial_response(
    template_name: str,
    request: Request,
    session: Session,
    settings_service: SettingsService,
    telemetry_service,
    operations_service,
    worker_control,
    search_service=None,
    config=None,
    *,
    message: Optional[str] = None,
    message_level: str = "info",
    error: Optional[str] = None,
    trigger_event: Optional[str] = None,
):
    response = _render_operations_template(
        template_name,
        request,
        session,
        settings_service,
        telemetry_service,
        operations_service,
        worker_control,
        search_service,
        config,
        message=message,
        message_level=message_level,
        error=error,
        is_partial=True,
    )
    if trigger_event and hasattr(response, "headers"):
        response.headers.setdefault("HX-Trigger", trigger_event)
    return response


def _managed_credentials_dir(settings_service: SettingsService) -> Path:
    return settings_service.secrets_root / "credentials"


def _safe_filename(original: Optional[str]) -> str:
    stem = Path(original or "credentials").stem or "credentials"
    suffix = Path(original or "credentials.json").suffix or ".json"
    clean_stem = re.sub(r"[^A-Za-z0-9_-]+", "-", stem).strip("-") or "credentials"
    timestamp = int(time.time())
    return f"{clean_stem}-{timestamp}{suffix if suffix else '.json'}"


def _actor_from_request(request: Request) -> str:
    return request.headers.get("x-actor") or WEB_ACTOR


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    """Render the settings dashboard."""
    return _render_full(request, session, settings_service)


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
        }
    )
    return templates.TemplateResponse(request, "operations.html", context)


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
        "partials/ops_queue_card.html",
        request,
        session,
        settings_service,
        telemetry_service,
        operations_service,
        worker_control,
    )


@router.get("/ui/operations/workers", response_class=HTMLResponse)
def operations_workers_card(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    telemetry_service=Depends(get_telemetry_service),
    operations_service=Depends(get_operations_service),
    worker_control=Depends(get_worker_control_service),
):
    return _operations_partial_response(
        "partials/ops_workers_card.html",
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
        "partials/ops_jobs_card.html",
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
        "partials/ops_operations_card.html",
        request,
        session,
        settings_service,
        telemetry_service,
        operations_service,
        worker_control,
    )


@router.get("/ui/operations/index", response_class=HTMLResponse)
def operations_index_card(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    telemetry_service=Depends(get_telemetry_service),
    operations_service=Depends(get_operations_service),
    worker_control=Depends(get_worker_control_service),
    search_service=Depends(get_search_service),
    config=Depends(get_config),
):
    return _operations_partial_response(
        "partials/ops_index_card.html",
        request,
        session,
        settings_service,
        telemetry_service,
        operations_service,
        worker_control,
        search_service=search_service,
        config=config,
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
            return _operations_partial_response(
                "partials/ops_operations_card.html",
                request,
                session,
                settings_service,
                telemetry_service,
                operations_service,
                worker_control,
                error=f"Invalid payload JSON: {exc.msg}",
            )

    try:
        operations_service.trigger(operation_type, parsed_payload, actor)
    except OperationConflict as exc:
        return _operations_partial_response(
            "partials/ops_operations_card.html",
            request,
            session,
            settings_service,
            telemetry_service,
            operations_service,
            worker_control,
            error=str(exc),
        )
    except ValueError as exc:
        return _operations_partial_response(
            "partials/ops_operations_card.html",
            request,
            session,
            settings_service,
            telemetry_service,
            operations_service,
            worker_control,
            error=str(exc),
        )

    return _operations_partial_response(
        "partials/ops_operations_card.html",
        request,
        session,
        settings_service,
        telemetry_service,
        operations_service,
        worker_control,
        message=f"Triggered {operation_type} job.",
        message_level="success",
        trigger_event="ops-refresh",
    )


@router.post("/ui/operations/jobs/{job_id}/cancel", response_class=HTMLResponse)
async def cancel_job_from_ui(
    request: Request,
    job_id: str,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    telemetry_service=Depends(get_telemetry_service),
    operations_service=Depends(get_operations_service),
    worker_control=Depends(get_worker_control_service),
):
    actor = _actor_from_request(request)
    try:
        operations_service.cancel_job(job_id, actor)
    except JobNotFoundError as exc:
        return _operations_partial_response(
            "partials/ops_jobs_card.html",
            request,
            session,
            settings_service,
            telemetry_service,
            operations_service,
            worker_control,
            error=str(exc),
        )

    return _operations_partial_response(
        "partials/ops_jobs_card.html",
        request,
        session,
        settings_service,
        telemetry_service,
        operations_service,
        worker_control,
        message=f"Cancelled job {job_id}.",
        message_level="success",
        trigger_event="ops-refresh",
    )


@router.post("/ui/workers/{action}", response_class=HTMLResponse)
async def worker_action_from_ui(
    request: Request,
    action: str,
    timeout: int = Form(300),
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    telemetry_service=Depends(get_telemetry_service),
    operations_service=Depends(get_operations_service),
    worker_control=Depends(get_worker_control_service),
):
    actor = _actor_from_request(request)
    action = action.lower()
    try:
        if action == "pause":
            worker_control.pause(session, actor)
            message = "Workers paused."
        elif action == "resume":
            worker_control.resume(session, actor)
            message = "Workers resumed."
        elif action == "drain":
            result = worker_control.drain(session, timeout, actor)
            message = f"Drain status: {result['status']}"
        else:
            return _operations_partial_response(
                "partials/ops_workers_card.html",
                request,
                session,
                settings_service,
                telemetry_service,
                operations_service,
                worker_control,
                error=f"Unsupported worker action '{action}'.",
            )
    except WorkerUnavailableError as exc:
        return _operations_partial_response(
            "partials/ops_workers_card.html",
            request,
            session,
            settings_service,
            telemetry_service,
            operations_service,
            worker_control,
            error=str(exc),
        )

    return _operations_partial_response(
        "partials/ops_workers_card.html",
        request,
        session,
        settings_service,
        telemetry_service,
        operations_service,
        worker_control,
        message=message,
        message_level="success",
        trigger_event="ops-refresh",
    )
@router.post("/ui/settings/credentials/path", response_class=HTMLResponse)
async def submit_credentials_path(
    request: Request,
    path: str = Form(...),
    notes: Optional[str] = Form(None),
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    actor = _actor_from_request(request)
    try:
        settings_service.update_credentials(
            session,
            path=path.strip(),
            notes=notes,
            actor=actor,
        )
    except SettingValidationError as exc:
        return _respond(
            "partials/credentials_card.html",
            request,
            session,
            settings_service,
            error=str(exc),
        )

    # Invalidate MCP categories cache
    _invalidate_categories_cache_safe()

    return _respond(
        "partials/credentials_card.html",
        request,
        session,
        settings_service,
        message="Credentials path saved and validated.",
        message_level="success",
        trigger_event="settings-changed",
    )


@router.post("/ui/settings/credentials/upload", response_class=HTMLResponse)
async def upload_credentials(
    request: Request,
    credential_file: UploadFile = File(...),
    notes: Optional[str] = Form(None),
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    actor = _actor_from_request(request)
    saved_path: Optional[Path] = None
    try:
        saved_path = await _persist_upload(credential_file, settings_service)
        settings_service.update_credentials(
            session,
            path=str(saved_path),
            notes=notes,
            actor=actor,
        )
    except (CredentialUploadError, SettingValidationError) as exc:
        if saved_path and saved_path.exists():
            saved_path.unlink(missing_ok=True)
        return _respond(
            "partials/credentials_card.html",
            request,
            session,
            settings_service,
            error=str(exc),
        )

    # Invalidate MCP categories cache
    _invalidate_categories_cache_safe()

    return _respond(
        "partials/credentials_card.html",
        request,
        session,
        settings_service,
        message=f"Credential uploaded to {saved_path}.",
        message_level="success",
        trigger_event="settings-changed",
    )


@router.post("/ui/settings/sheets", response_class=HTMLResponse)
async def update_sheets_settings(
    request: Request,
    spreadsheet_id: str = Form(...),
    experiences_tab: str = Form("Experiences"),
    manuals_tab: str = Form("Manuals"),
    categories_tab: str = Form("Categories"),
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    actor = _actor_from_request(request)
    try:
        settings_service.update_sheets(
            session,
            spreadsheet_id=spreadsheet_id,
            experiences_tab=experiences_tab,
            manuals_tab=manuals_tab,
            categories_tab=categories_tab,
            actor=actor,
        )
    except SettingValidationError as exc:
        return _respond(
            "partials/sheets_card.html",
            request,
            session,
            settings_service,
            error=str(exc),
        )

    # Invalidate MCP categories cache
    _invalidate_categories_cache_safe()

    return _respond(
        "partials/sheets_card.html",
        request,
        session,
        settings_service,
        message="Sheet configuration saved.",
        message_level="success",
        trigger_event="settings-changed",
    )


@router.post("/ui/settings/models", response_class=HTMLResponse)
async def update_model_preferences(
    request: Request,
    embedding_repo: Optional[str] = Form(None),
    embedding_quant: Optional[str] = Form(None),
    reranker_repo: Optional[str] = Form(None),
    reranker_quant: Optional[str] = Form(None),
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    actor = _actor_from_request(request)
    settings_service.update_models(
        session,
        embedding_repo=embedding_repo,
        embedding_quant=embedding_quant,
        reranker_repo=reranker_repo,
        reranker_quant=reranker_quant,
        actor=actor,
    )

    # Invalidate MCP categories cache
    _invalidate_categories_cache_safe()

    return _respond(
        "partials/models_card.html",
        request,
        session,
        settings_service,
        message="Model preferences updated.",
        message_level="success",
        trigger_event="settings-changed",
    )


@router.get("/ui/settings/diagnostics", response_class=HTMLResponse)
def diagnostics_panel(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    return _render_partial("partials/diagnostics_panel.html", request, session, settings_service)


@router.post("/ui/settings/diagnostics", response_class=HTMLResponse)
async def diagnostics_probe(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    actor = _actor_from_request(request)
    snapshot = settings_service.snapshot(session)
    credentials = snapshot.get("credentials") if snapshot else None
    if not credentials or not credentials.get("path"):
        return _respond(
            "partials/diagnostics_panel.html",
            request,
            session,
            settings_service,
            error="No credentials configured to validate.",
        )

    try:
        settings_service.update_credentials(
            session,
            path=credentials["path"],
            notes=None,
            actor=actor,
        )
    except SettingValidationError as exc:
        return _respond(
            "partials/diagnostics_panel.html",
            request,
            session,
            settings_service,
            error=str(exc),
        )

    return _respond(
        "partials/diagnostics_panel.html",
        request,
        session,
        settings_service,
        message="Connectivity check refreshed.",
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
    return _render_partial("partials/audit_log.html", request, session, settings_service)


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
        return _respond(
            "partials/backup_card.html",
            request,
            session,
            settings_service,
            error="Backup JSON payload is required.",
        )

    try:
        payload = json.loads(backup_json)
    except json.JSONDecodeError as exc:
        return _respond(
            "partials/backup_card.html",
            request,
            session,
            settings_service,
            error=f"Invalid JSON: {exc.msg}.",
        )

    if not isinstance(payload, dict):
        return _respond(
            "partials/backup_card.html",
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
            return _respond(
                "partials/backup_card.html",
                request,
                session,
                settings_service,
                error=f"Credential restore failed: {exc}",
            )

    sheets = payload.get("sheets")
    if isinstance(sheets, dict) and sheets.get("spreadsheet_id"):
        try:
            settings_service.update_sheets(
                session,
                spreadsheet_id=sheets.get("spreadsheet_id", ""),
                experiences_tab=sheets.get("experiences_tab", "Experiences"),
                manuals_tab=sheets.get("manuals_tab", "Manuals"),
                categories_tab=sheets.get("categories_tab", "Categories"),
                actor=actor,
            )
            applied.append("sheets")
        except SettingValidationError as exc:
            return _respond(
                "partials/backup_card.html",
                request,
                session,
                settings_service,
                error=f"Sheets restore failed: {exc}",
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
        return _respond(
            "partials/backup_card.html",
            request,
            session,
            settings_service,
            error="No restorable sections found in backup payload.",
        )

    applied_msg = ", ".join(applied)
    # Invalidate MCP categories cache (settings changed)
    _invalidate_categories_cache_safe()
    return _respond(
        "partials/backup_card.html",
        request,
        session,
        settings_service,
        message=f"Restored sections: {applied_msg}.",
        message_level="success",
        trigger_event="settings-changed",
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
    cycles: int = Query(0, ge=0, le=100, description="Number of update cycles before closing (0=infinite)"),
):
    from src.api_server import db  # Local import to avoid circular dependency

    async def event_generator():
        emitted = 0
        while True:
            session = db.get_session()
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

            render_context = {
                **context,
                "request": request,
                "message": None,
                "message_level": "info",
                "error": None,
                "is_partial": True,
            }
            queue_html = templates.get_template("partials/ops_queue_card.html").render(render_context)
            workers_html = templates.get_template("partials/ops_workers_card.html").render(render_context)
            jobs_html = templates.get_template("partials/ops_jobs_card.html").render(render_context)
            index_html = templates.get_template("partials/ops_index_card.html").render(render_context)
            controls_html = templates.get_template("partials/ops_operations_card.html").render(render_context)

            yield {"event": "queue", "data": queue_html}
            yield {"event": "workers", "data": workers_html}
            yield {"event": "jobs", "data": jobs_html}
            yield {"event": "index", "data": index_html}
            yield {"event": "controls", "data": controls_html}

            emitted += 1
            if cycles and emitted >= cycles:
                break

            try:
                await asyncio.sleep(5)
            except asyncio.CancelledError:  # pragma: no cover - disconnect
                break

    return EventSourceResponse(event_generator())


@router.get("/ui/index/download")
def download_index_snapshot(
    background_tasks: BackgroundTasks,
    config=Depends(get_config),
):
    archive_path = _create_index_archive(config)
    filename = f"chl-faiss-snapshot-{int(time.time())}.zip"

    def _cleanup(path: str):  # pragma: no cover - best effort cleanup
        try:
            os.remove(path)
        except OSError:
            pass

    background_tasks.add_task(_cleanup, str(archive_path))
    return FileResponse(
        archive_path,
        filename=filename,
        media_type="application/zip",
        background=background_tasks,
    )


@router.post("/ui/index/upload", response_class=HTMLResponse)
async def upload_index_snapshot(
    request: Request,
    snapshot: UploadFile = File(...),
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    telemetry_service=Depends(get_telemetry_service),
    operations_service=Depends(get_operations_service),
    worker_control=Depends(get_worker_control_service),
    search_service=Depends(get_search_service),
    config=Depends(get_config),
):
    actor = _actor_from_request(request)
    archive_path: Optional[Path] = None
    try:
        archive_path = await _persist_archive_upload(snapshot)
        restore_result = _restore_index_archive(archive_path, config, search_service)
    except IndexUploadError as exc:
        # Audit blocked upload attempts for security visibility
        session.add(
            AuditLog(
                event_type="index.snapshot.upload_blocked",
                actor=actor,
                context=json.dumps({"error": str(exc)}, ensure_ascii=False),
                created_at=utc_now(),
            )
        )
        if archive_path and archive_path.exists():
            archive_path.unlink(missing_ok=True)
        return _operations_partial_response(
            "partials/ops_index_card.html",
            request,
            session,
            settings_service,
            telemetry_service,
            operations_service,
            worker_control,
            search_service=search_service,
            config=config,
            error=str(exc),
        )

    if archive_path and archive_path.exists():
        archive_path.unlink(missing_ok=True)

    session.add(
        AuditLog(
            event_type="index.snapshot.uploaded",
            actor=actor,
            context=json.dumps(restore_result, ensure_ascii=False),
            created_at=utc_now(),
        )
    )

    reload_note = " Reloaded index." if restore_result.get("reloaded") else " Restart service to apply changes."
    message = f"Uploaded snapshot ({len(restore_result.get('copied', []))} files)." + reload_note
    return _operations_partial_response(
        "partials/ops_index_card.html",
        request,
        session,
        settings_service,
        telemetry_service,
        operations_service,
        worker_control,
        search_service=search_service,
        config=config,
        message=message,
        message_level="success",
        trigger_event="ops-refresh",
    )


async def _persist_upload(credential_file: UploadFile, settings_service: SettingsService) -> Path:
    """Validate and persist an uploaded credential file."""
    raw = await credential_file.read(MAX_UPLOAD_BYTES + 1)
    if not raw:
        raise CredentialUploadError("Uploaded credential is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise CredentialUploadError("Credential file exceeds 512 KiB limit.")

    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise CredentialUploadError("Credential file must be UTF-8 encoded JSON.") from None

    try:
        json.loads(decoded)
    except json.JSONDecodeError as exc:
        raise CredentialUploadError(f"Credential JSON invalid: {exc.msg}.") from exc

    managed_dir = _managed_credentials_dir(settings_service)
    managed_dir.mkdir(parents=True, exist_ok=True)
    dest_name = _safe_filename(credential_file.filename)
    dest_path = managed_dir / dest_name

    with dest_path.open("w", encoding="utf-8") as fh:
        fh.write(decoded)

    os.chmod(dest_path, 0o600)
    return dest_path


def _create_index_archive(config) -> Path:
    index_dir = _index_dir(config)
    if not index_dir or not index_dir.exists():
        raise HTTPException(status_code=404, detail="FAISS index directory not found.")

    files = [path for path in index_dir.iterdir() if path.is_file()]
    if not files:
        raise HTTPException(status_code=400, detail="No FAISS index files to download.")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, arcname=path.name)
    return Path(tmp.name)


async def _persist_archive_upload(upload_file: UploadFile) -> Path:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    size = 0
    try:
        while True:
            chunk = await upload_file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_INDEX_ARCHIVE_BYTES:
                raise IndexUploadError("Archive exceeds 512 MiB limit.")
            tmp.write(chunk)
    finally:
        tmp.flush()
        tmp.close()

    if size == 0:
        os.unlink(tmp.name)
        raise IndexUploadError("Uploaded archive is empty.")
    return Path(tmp.name)


def _reload_index_from_disk(search_service) -> bool:
    if not search_service:
        return False
    try:
        vector_provider = search_service.get_vector_provider()
    except Exception:  # pragma: no cover - defensive
        return False
    if not vector_provider:
        return False
    manager = getattr(vector_provider, "index_manager", None)
    if not manager:
        return False

    underlying = getattr(manager, "_manager", None)
    if not underlying:
        return False

    lock = getattr(manager, "_lock", None)

    def _reload():
        underlying._index = None
        underlying._load_or_create_index()

    try:
        if lock:
            with lock:
                _reload()
        else:
            _reload()
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to hot-reload index: %s", exc, exc_info=True)
        return False


def _validate_index_member(member: zipfile.ZipInfo) -> str:
    """Validate a single ZIP member. Returns sanitized filename (basename)."""
    rel_name = Path(member.filename).name
    if not rel_name:
        raise IndexUploadError("Archive contains empty filename.")

    # Normalize and check extension (case-insensitive)
    rel_lower = rel_name.lower()
    if not any(rel_lower.endswith(s) for s in ALLOWED_INDEX_FILE_SUFFIXES):
        raise IndexUploadError(f"Unsupported file '{rel_name}'.")

    # Per-file size limit (archive already limited, but double-check)
    if member.file_size > MAX_INDEX_ARCHIVE_BYTES:
        raise IndexUploadError(f"File too large: {rel_name}")

    # Basic path traversal checks on original name
    if ".." in member.filename or member.filename.startswith("/"):
        raise IndexUploadError(f"Invalid path in archive: {member.filename}")

    return rel_name


def _restore_index_archive(archive_path: Path, config, search_service) -> dict:
    index_dir = _index_dir(config)
    if not index_dir:
        raise IndexUploadError("FAISS index path is not configured.")
    index_dir.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            if not members:
                raise IndexUploadError("Archive did not contain any files.")
            # Validate all members before any extraction
            validated_names = [_validate_index_member(m) for m in members]
            temp_dir = Path(tempfile.mkdtemp())
            # Secure, file-by-file extraction
            for member, rel_name in zip(members, validated_names):
                dest = (temp_dir / rel_name).resolve()
                # Ensure we only write under temp_dir
                if dest.parent != temp_dir.resolve():
                    raise IndexUploadError("Path resolution failed.")
                with archive.open(member, "r") as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
    except zipfile.BadZipFile as exc:
        raise IndexUploadError("Uploaded file is not a valid ZIP archive.") from exc

    copied = []
    backup_dir = None
    base_temp = temp_dir.resolve()

    try:
        # Use the previously validated list of filenames
        for rel_name in validated_names:
            src_path = (temp_dir / rel_name).resolve()
            if not str(src_path).startswith(str(base_temp)):
                raise IndexUploadError("Archive attempted path traversal.")
            dest_path = index_dir / rel_name
            if dest_path.exists():
                if backup_dir is None:
                    backup_dir = index_dir / f".preupload_{int(time.time())}"
                    backup_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest_path, backup_dir / rel_name)
            shutil.copy2(src_path, dest_path)
            copied.append(rel_name)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    reloaded = _reload_index_from_disk(search_service)
    return {
        "copied": copied,
        "reloaded": reloaded,
        "backup_dir": str(backup_dir) if backup_dir else None,
    }
