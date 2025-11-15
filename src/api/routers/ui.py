"""Server-rendered UI endpoints for settings and operations management."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional
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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from src.api.dependencies import (
    get_config,
    get_db_session,
    get_operations_service,
    get_search_service,
    get_settings_service,
    get_telemetry_service,
    get_worker_control_service,
)
from src.services import gpu_installer
from src.services.settings_service import SettingValidationError, SettingsService
from src.services.operations_service import OperationConflict, JobNotFoundError
from src.services.worker_control import WorkerUnavailableError
from src.storage.repository import AuditLogRepository
from src.storage.schema import AuditLog, utc_now


TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "web" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

MAX_INDEX_ARCHIVE_BYTES = 512 * 1024 * 1024  # 512 MiB cap for FAISS snapshots
WEB_ACTOR = "web-ui"
ALLOWED_INDEX_FILE_SUFFIXES = frozenset([".index", ".json", ".backup"])  # case-insensitive

logger = logging.getLogger(__name__)

EMBEDDING_CHOICES = [
    {
        "repo": "Qwen/Qwen3-Embedding-0.6B-GGUF",
        "quant": "Q8_0",
        "label": "0.6B · Q8_0 · ~600 MB",
        "tag": "minimum",
        "notes": "CPU friendly; fastest to rebuild",
    },
    {
        "repo": "Qwen/Qwen3-Embedding-4B-GGUF",
        "quant": "Q4_K_M",
        "label": "4B · Q4_K_M · ~2.5 GB",
        "tag": "recommended",
        "notes": "Best balance of speed vs quality",
    },
    {
        "repo": "Qwen/Qwen3-Embedding-4B-GGUF",
        "quant": "Q5_K_M",
        "label": "4B · Q5_K_M · ~2.9 GB",
        "tag": None,
        "notes": "Sharper vectors; needs more RAM",
    },
    {
        "repo": "Qwen/Qwen3-Embedding-4B-GGUF",
        "quant": "Q8_0",
        "label": "4B · Q8_0 · ~4.3 GB",
        "tag": None,
        "notes": "Near-FP16 quality",
    },
    {
        "repo": "Qwen/Qwen3-Embedding-8B-GGUF",
        "quant": "Q4_K_M",
        "label": "8B · Q4_K_M · ~5 GB",
        "tag": None,
        "notes": "Best quality if you have VRAM",
    },
]

RERANKER_CHOICES = [
    {
        "repo": "Mungert/Qwen3-Reranker-0.6B-GGUF",
        "quant": "Q4_K_M",
        "label": "0.6B · Q4_K_M · ~300 MB",
        "tag": "minimum",
        "notes": "Great accuracy on CPU",
    },
    {
        "repo": "Mungert/Qwen3-Reranker-0.6B-GGUF",
        "quant": "Q8_0",
        "label": "0.6B · Q8_0 · ~600 MB",
        "tag": None,
        "notes": "Highest quality without GPU",
    },
    {
        "repo": "Mungert/Qwen3-Reranker-4B-GGUF",
        "quant": "Q4_K_M",
        "label": "4B · Q4_K_M · ~2.5 GB",
        "tag": "recommended",
        "notes": "Use when you have GPU VRAM",
    },
    {
        "repo": "Mungert/Qwen3-Reranker-4B-GGUF",
        "quant": "Q8_0",
        "label": "4B · Q8_0 · ~4.3 GB",
        "tag": None,
        "notes": "Highest fidelity (GPU)",
    },
]


class IndexUploadError(Exception):
    """Raised when an index snapshot upload fails validation."""


router = APIRouter(tags=["ui"])


def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request", "").lower() == "true"


def _invalidate_categories_cache_safe() -> None:
    """Best-effort MCP categories cache invalidation (may run out-of-process)."""
    import sys

    mcp_server = sys.modules.get("src.server")
    if not mcp_server:
        # Don't import lazily here; importing would try to auto-start the MCP server and
        # block the current FastAPI request. Skipping this keeps the UI responsive when
        # the MCP stack isn't co-hosted.
        logger.debug("MCP server not loaded; skipping cache invalidation.")
        return

    invalidate = getattr(mcp_server, "invalidate_categories_cache", None)
    if not invalidate:
        return

    try:
        invalidate()
    except Exception:
        # Best effort only
        logger.debug("MCP cache invalidation failed", exc_info=True)


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
    diagnostics_dict = {name: status.to_dict() for name, status in diagnostics.items()}
    context = {
        "request": request,
        "snapshot": snapshot,
        "message": message,
        "message_level": message_level,
        "error": error,
        "secrets_root": str(settings_service.secrets_root),
        "diagnostics": diagnostics_dict,
        "audit_entries": _recent_audit_entries(session),
        "active_page": "settings",
        "default_scripts_config_path": str(_default_scripts_config_path()),
        "embedding_choices": EMBEDDING_CHOICES,
        "reranker_choices": RERANKER_CHOICES,
        "env_config_status": _get_env_config_status(snapshot=snapshot, diagnostics=diagnostics_dict),
    }
    context.update(_build_gpu_runtime_context())
    return context


def _build_gpu_runtime_context() -> Dict[str, object]:
    priority_raw = os.getenv("CHL_GPU_PRIORITY")
    priority = gpu_installer.parse_gpu_priority(priority_raw)
    state = gpu_installer.load_gpu_state()
    suffix = gpu_installer.recommended_wheel_suffix(state) if state else None
    prereq = gpu_installer.prerequisite_check(state)
    prereq_status = prereq.get("status") if isinstance(prereq, dict) else "unknown"
    install_allowed = bool(suffix) and prereq_status in {"ok", "warn"}
    return {
        "gpu_state": state,
        "gpu_priority": priority,
        "gpu_priority_raw": priority_raw,
        "gpu_recommended_suffix": suffix,
        "gpu_install_supported": install_allowed,
        "gpu_backend_override": os.getenv("CHL_GPU_BACKEND"),
        "gpu_prereq": prereq,
    }


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
    return templates.TemplateResponse("settings.html", context)


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
    return templates.TemplateResponse(template_name, context)


def _render_gpu_card(
    request: Request,
    *,
    message: Optional[str] = None,
    message_level: str = "info",
    log: Optional[str] = None,
    prompt: Optional[str] = None,
):
    context = _build_gpu_runtime_context()
    context.update(
        {
            "request": request,
            "gpu_message": message,
            "gpu_message_level": message_level,
            "gpu_log": log,
            "gpu_prompt_text": prompt,
        }
    )
    return templates.TemplateResponse("partials/settings_gpu_runtime.html", context)


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
        "env_config_status": _get_env_config_status(),
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
    return templates.TemplateResponse(template_name, context)


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


def _render_operation_result(
    template_name: str,
    request: Request,
    session: Session,
    settings_service: SettingsService,
    telemetry_service,
    operations_service,
    worker_control,
    *,
    message: Optional[str] = None,
    message_level: str = "info",
    error: Optional[str] = None,
    trigger_event: Optional[str] = None,
):
    """Render operation result template with appropriate context.

    For config_status_card, includes both settings and job_summaries context.
    For ops_operations_card, uses full operations context.
    """
    if template_name == "partials/config_status_card.html":
        # Build settings context with job_summaries
        context = _build_settings_context(
            request,
            session,
            settings_service,
            message=message,
            message_level=message_level,
            error=error,
        )
        # Add job_summaries from operations
        jobs = operations_service.list_recent(session, limit=10)
        job_summaries = _summarize_job_runs(jobs, operations_service.last_runs_by_type(session))
        context["job_summaries"] = job_summaries
        context["is_partial"] = True
        response = templates.TemplateResponse(template_name, context)
    else:
        # Use full operations context
        response = _render_operations_template(
            template_name,
            request,
            session,
            settings_service,
            telemetry_service,
            operations_service,
            worker_control,
            None,
            None,
            message=message,
            message_level=message_level,
            error=error,
            is_partial=True,
        )

    if trigger_event and hasattr(response, "headers"):
        response.headers.setdefault("HX-Trigger", trigger_event)
    return response


def _default_scripts_config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "scripts" / "scripts_config.yaml"


def _actor_from_request(request: Request) -> str:
    return request.headers.get("x-actor") or WEB_ACTOR


def _get_model_info() -> dict:
    """Get current model configuration from model_selection.json or environment."""
    import json
    from pathlib import Path

    model_info = {
        "state": "warn",
        "headline": "Not configured",
        "embedding_repo": None,
        "embedding_quant": None,
        "reranker_repo": None,
        "reranker_quant": None,
        "embedding_size": None,
    }

    # Try to load from model_selection.json (preferred)
    project_root = Path(__file__).resolve().parents[3]
    model_selection_path = project_root / "data" / "model_selection.json"

    if model_selection_path.exists():
        try:
            with model_selection_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            model_info["embedding_repo"] = data.get("embedding_repo")
            model_info["embedding_quant"] = data.get("embedding_quant")
            model_info["reranker_repo"] = data.get("reranker_repo")
            model_info["reranker_quant"] = data.get("reranker_quant")
            model_info["embedding_size"] = data.get("embedding_size")

            if all([model_info["embedding_repo"], model_info["embedding_quant"],
                    model_info["reranker_repo"], model_info["reranker_quant"]]):
                model_info["state"] = "ok"
                model_info["headline"] = "Loaded"
        except (json.JSONDecodeError, OSError):
            pass

    # Fallback to environment variables
    if not model_info["embedding_repo"]:
        model_info["embedding_repo"] = os.getenv("CHL_EMBEDDING_REPO")
        model_info["embedding_quant"] = os.getenv("CHL_EMBEDDING_QUANT")
        model_info["reranker_repo"] = os.getenv("CHL_RERANKER_REPO")
        model_info["reranker_quant"] = os.getenv("CHL_RERANKER_QUANT")

    return model_info


def _get_env_config_status(
    *,
    snapshot: Optional[dict] = None,
    diagnostics: Optional[dict] = None,
) -> dict:
    """Read configuration from environment variables for display.

    Returns a dict with configuration status suitable for template rendering.
    """
    import os
    from pathlib import Path

    diagnostics = diagnostics or {}
    snapshot = snapshot or {}

    credentials_path = os.getenv("GOOGLE_CREDENTIAL_PATH", "")
    import_sheet_id = os.getenv("IMPORT_SPREADSHEET_ID", "")
    export_sheet_id = os.getenv("EXPORT_SPREADSHEET_ID", "")

    # Worksheet names with defaults
    import_worksheets = ", ".join([
        os.getenv("IMPORT_WORKSHEET_CATEGORIES", "Categories"),
        os.getenv("IMPORT_WORKSHEET_EXPERIENCES", "Experiences"),
        os.getenv("IMPORT_WORKSHEET_MANUALS", "Manuals"),
    ])

    export_worksheets = ", ".join([
        os.getenv("EXPORT_WORKSHEET_CATEGORIES", "Categories"),
        os.getenv("EXPORT_WORKSHEET_EXPERIENCES", "Experiences"),
        os.getenv("EXPORT_WORKSHEET_MANUALS", "Manuals"),
    ])

    # Check credential file status
    credentials_state = "error"
    credentials_status = "Not configured"
    credentials_detail = None
    cred_file = None

    if not credentials_path:
        cred_snapshot = snapshot.get("credentials") or {}
        credentials_path = cred_snapshot.get("path") or ""

    if credentials_path:
        cred_file = Path(credentials_path)
        if not cred_file.is_absolute():
            # Resolve relative paths from project root
            project_root = Path(__file__).resolve().parents[3]
            cred_file = (project_root / cred_file).resolve()

        if cred_file.exists():
            if cred_file.is_file():
                try:
                    # Check permissions
                    import stat
                    perms = stat.S_IMODE(cred_file.stat().st_mode)
                    if perms & 0o077:
                        credentials_state = "warn"
                        credentials_status = "Insecure permissions"
                        credentials_detail = f"Run: chmod 600 {cred_file}"
                    else:
                        credentials_state = "ok"
                        credentials_status = "Ready"
                        credentials_detail = None
                except OSError:
                    credentials_state = "warn"
                    credentials_status = "Cannot check permissions"
            else:
                credentials_state = "error"
                credentials_status = "Path is not a file"
        else:
            credentials_state = "error"
            credentials_status = "File not found"
            credentials_detail = str(cred_file)

    # Fall back to snapshot data when .env isn't populated yet
    sheets_snapshot = snapshot.get("sheets") or {}
    if not import_sheet_id:
        import_sheet_id = (
            sheets_snapshot.get("import_spreadsheet_id")
            or sheets_snapshot.get("experiences_sheet_id")
            or sheets_snapshot.get("manuals_sheet_id")
            or sheets_snapshot.get("category_sheet_id")
            or ""
        )
    if not export_sheet_id:
        export_sheet_id = (
            sheets_snapshot.get("export_spreadsheet_id")
            or sheets_snapshot.get("experiences_sheet_id")
            or sheets_snapshot.get("manuals_sheet_id")
            or sheets_snapshot.get("category_sheet_id")
            or ""
        )

    # Optionally align with diagnostics (which already handle CPU/GPU nuances)
    cred_diag = diagnostics.get("credentials") if diagnostics else None
    sheets_diag = diagnostics.get("sheets") if diagnostics else None
    if cred_diag:
        credentials_state = cred_diag.get("state", credentials_state)
        credentials_status = cred_diag.get("headline", credentials_status)
        credentials_detail = cred_diag.get("detail", credentials_detail)
    if sheets_diag:
        sheets_state = sheets_diag.get("state", "error")
        if sheets_diag.get("state") == "ok":
            import_sheet_id = import_sheet_id or (sheets_diag.get("detail") or "configured")
            export_sheet_id = export_sheet_id or (sheets_diag.get("detail") or "configured")
    else:
        sheets_state = "ok" if import_sheet_id and export_sheet_id else "error"

    # Determine overall health/headline
    credentials_ready = credentials_state != "error"
    sheets_ready = sheets_state != "error"
    if credentials_state == "ok" and sheets_state == "ok":
        overall_state = "ok"
        overall_headline = "Configuration ready"
    elif not credentials_ready or not sheets_ready:
        overall_state = "error"
        overall_headline = "Missing configuration"
    else:
        overall_state = "warn"
        overall_headline = "Configuration warning"

    # Model information from Config or model_selection.json
    model_info = _get_model_info()

    return {
        "state": overall_state,
        "headline": overall_headline,
        "credentials_path": str(cred_file) if credentials_path else None,
        "credentials_state": credentials_state,
        "credentials_status": credentials_status,
        "credentials_detail": credentials_detail,
        "import_sheet_id": import_sheet_id if import_sheet_id else None,
        "import_worksheets": import_worksheets,
        "export_sheet_id": export_sheet_id if export_sheet_id else None,
        "export_worksheets": export_worksheets,
        "model_info": model_info,
    }


def _parse_model_choice(choice: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not choice:
        return None, None
    parts = choice.split("|", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return parts[0], None


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    operations_service=Depends(get_operations_service),
    config=Depends(get_config),
):
    """Render the settings dashboard.

    Dynamically selects template based on search mode:
    - sqlite_only mode: settings_cpu.html (simplified, keyword search guidance)
    - auto/vector mode: settings.html (full GPU features)
    """
    context = _build_settings_context(request, session, settings_service)
    # Add job_summaries for import/export status display
    jobs = operations_service.list_recent(session, limit=10)
    job_summaries = _summarize_job_runs(jobs, operations_service.last_runs_by_type(session))
    context["job_summaries"] = job_summaries
    context["is_partial"] = False
    context["search_mode"] = config.search_mode

    # Select template based on search mode
    template_name = "settings_cpu.html" if config.search_mode == "sqlite_only" else "settings.html"
    return templates.TemplateResponse(template_name, context)


@router.get("/ui/settings/gpu/card", response_class=HTMLResponse)
def settings_gpu_card(request: Request):
    return _render_gpu_card(request)


@router.post("/ui/settings/gpu/detect", response_class=HTMLResponse)
def settings_gpu_detect(request: Request):
    priority = gpu_installer.parse_gpu_priority(os.getenv("CHL_GPU_PRIORITY"))
    backend_override = os.getenv("CHL_GPU_BACKEND")
    try:
        state, cached = gpu_installer.ensure_gpu_state(priority, backend_override, True)
        source = "cached" if cached else "detected"
        message = f"{state.get('backend', 'cpu')} backend {source} successfully."
        level = "success"
        log = None
    except gpu_installer.GPUInstallerError as exc:
        return _render_gpu_card(request, message=str(exc), message_level="error")
    except Exception as exc:  # noqa: BLE001
        logger.exception("GPU detection failed")
        return _render_gpu_card(request, message=str(exc), message_level="error")

    return _render_gpu_card(request, message=message, message_level=level, log=log)


@router.post("/ui/settings/gpu/install", response_class=HTMLResponse)
def settings_gpu_install(request: Request):
    priority = gpu_installer.parse_gpu_priority(os.getenv("CHL_GPU_PRIORITY"))
    backend_override = os.getenv("CHL_GPU_BACKEND")
    try:
        state, _ = gpu_installer.ensure_gpu_state(priority, backend_override, False)
    except gpu_installer.GPUInstallerError as exc:
        return _render_gpu_card(request, message=str(exc), message_level="error")

    backend = (state or {}).get("backend", "cpu")
    suffix = gpu_installer.recommended_wheel_suffix(state) if state else None
    prereq = gpu_installer.prerequisite_check(state)
    prereq_status = prereq.get("status") if isinstance(prereq, dict) else "unknown"

    if prereq_status not in {"ok", "warn"}:
        return _render_gpu_card(
            request,
            message=prereq.get("message"),
            message_level="warn",
        )

    if backend == "cpu" or not suffix:
        return _render_gpu_card(
            request,
            message="No GPU backend detected. Install GPU drivers or set CHL_GPU_BACKEND before retrying.",
            message_level="warn",
        )

    success, install_log = gpu_installer.install_llama_cpp(state)
    if not success:
        return _render_gpu_card(
            request,
            message="GPU wheel installation failed",
            message_level="error",
            log=install_log,
        )

    verify_ok, verify_log = gpu_installer.verify_llama_install(state)
    if verify_ok:
        return _render_gpu_card(
            request,
            message="GPU runtime installed and verified",
            message_level="success",
            log=install_log,
        )

    combined_log = (install_log or "") + ("\n" + verify_log if verify_log else "")
    return _render_gpu_card(
        request,
        message="Installed GPU wheel but verification failed. See log for details.",
        message_level="warn",
        log=combined_log,
    )


@router.post("/ui/settings/gpu/support-prompt", response_class=HTMLResponse)
def settings_gpu_support_prompt(request: Request):
    state = gpu_installer.load_gpu_state()
    prereq = gpu_installer.prerequisite_check(state)
    verify_log = None
    if state and state.get("status") == "needs_attention":
        verify_log = state.get("install_log")
    prompt = gpu_installer.build_support_prompt(state, prereq, verify_log=verify_log)
    return _render_gpu_card(
        request,
        message="Copy the prompt below and paste it into ChatGPT, Claude, or another assistant to get the latest driver steps.",
        message_level="info",
        prompt=prompt,
    )


@router.get("/ui/settings/config-status", response_class=HTMLResponse)
def settings_config_status_card(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    telemetry_service=Depends(get_telemetry_service),
    operations_service=Depends(get_operations_service),
    worker_control=Depends(get_worker_control_service),
):
    """HTMX endpoint to refresh the configuration status card."""
    return _render_operation_result(
        "partials/config_status_card.html",
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
):
    """Server-sent events stream powering dashboard live updates."""
    from src.api_server import db  # Local import to avoid circulars at module import time

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

    session_factory = db.get_session

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

            yield f"event: queue\ndata: {json.dumps(queue_payload, ensure_ascii=False)}\n\n"
            yield f"event: index\ndata: {json.dumps(index_payload or {}, ensure_ascii=False)}\n\n"
            yield f"event: controls\ndata: {json.dumps(controls_payload, ensure_ascii=False)}\n\n"

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
            "search_mode": config.search_mode,
        }
    )
    template_name = "operations_cpu.html" if config.search_mode == "sqlite_only" else "operations_gpu.html"
    return templates.TemplateResponse(template_name, context)


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


@router.get("/ui/operations/models", response_class=HTMLResponse)
def operations_models_card(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    telemetry_service=Depends(get_telemetry_service),
    operations_service=Depends(get_operations_service),
    worker_control=Depends(get_worker_control_service),
):
    return _operations_partial_response(
        "partials/ops_models_card.html",
        request,
        session,
        settings_service,
        telemetry_service,
        operations_service,
        worker_control,
    )


@router.get("/ui/operations/models/change", response_class=HTMLResponse)
def get_model_change_modal(
    request: Request,
    session: Session = Depends(get_db_session),
):
    """Render the model change modal with current selections and impact estimate."""
    from src.storage.repository import ExperienceRepository, ManualRepository

    # Get current model info
    current_models = _get_model_info()

    # Get impact estimate (count of items that will need re-embedding)
    exp_repo = ExperienceRepository(session)
    manual_repo = ManualRepository(session)

    experience_count = exp_repo.count()
    manual_count = manual_repo.count()
    total_items = experience_count + manual_count

    # Rough estimate: ~2 seconds per item for embedding generation
    estimated_seconds = total_items * 2
    if estimated_seconds < 60:
        estimated_time = f"~{estimated_seconds}s"
    elif estimated_seconds < 3600:
        estimated_time = f"~{estimated_seconds // 60}m"
    else:
        estimated_time = f"~{estimated_seconds // 3600}h {(estimated_seconds % 3600) // 60}m"

    impact_estimate = {
        "experience_count": experience_count,
        "manual_count": manual_count,
        "total_items": total_items,
        "estimated_time": estimated_time,
    }

    context = {
        "request": request,
        "current_models": current_models,
        "embedding_choices": EMBEDDING_CHOICES,
        "reranker_choices": RERANKER_CHOICES,
        "impact_estimate": impact_estimate,
    }

    return templates.TemplateResponse("partials/model_change_modal.html", context)


def _check_disk_space(min_gb: float = 10.0) -> tuple[bool, str]:
    """Check if sufficient disk space is available.

    Args:
        min_gb: Minimum required disk space in GB

    Returns:
        Tuple of (has_space, message)
    """
    try:
        # Check available space in user's home directory (where HF models are cached)
        home = Path.home()
        stat = shutil.disk_usage(home)
        available_gb = stat.free / (1024 ** 3)

        if available_gb < min_gb:
            return False, f"Insufficient disk space: {available_gb:.1f} GB available, {min_gb:.1f} GB required"

        return True, f"{available_gb:.1f} GB available"
    except Exception as e:
        logger.warning(f"Failed to check disk space: {e}")
        # Continue anyway if check fails
        return True, "Could not verify disk space"


@router.post("/ui/operations/models/change", response_class=HTMLResponse)
async def post_model_change(
    request: Request,
    embedding_choice: str = Form(...),
    reranker_choice: str = Form(...),
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    telemetry_service=Depends(get_telemetry_service),
    operations_service=Depends(get_operations_service),
    worker_control=Depends(get_worker_control_service),
):
    """Handle model change request and trigger re-embedding job."""
    actor = _actor_from_request(request)

    # Check disk space before proceeding
    has_space, space_msg = _check_disk_space(min_gb=10.0)
    if not has_space:
        return _operations_partial_response(
            "partials/ops_models_card.html",
            request,
            session,
            settings_service,
            telemetry_service,
            operations_service,
            worker_control,
            error=f"Cannot change models: {space_msg}. Free up disk space and try again.",
        )

    # Parse model choices
    embedding_repo, embedding_quant = _parse_model_choice(embedding_choice)
    reranker_repo, reranker_quant = _parse_model_choice(reranker_choice)

    if not all([embedding_repo, embedding_quant, reranker_repo, reranker_quant]):
        return _operations_partial_response(
            "partials/ops_models_card.html",
            request,
            session,
            settings_service,
            telemetry_service,
            operations_service,
            worker_control,
            error="Invalid model selection",
        )

    # Save model selection to model_selection.json
    try:
        model_selection_data = {
            "embedding_repo": embedding_repo,
            "embedding_quant": embedding_quant,
            "reranker_repo": reranker_repo,
            "reranker_quant": reranker_quant,
        }

        project_root = Path(__file__).resolve().parents[3]
        model_selection_path = project_root / "data" / "model_selection.json"
        model_selection_path.parent.mkdir(parents=True, exist_ok=True)

        with model_selection_path.open("w", encoding="utf-8") as f:
            json.dump(model_selection_data, f, indent=2, ensure_ascii=False)

        # Log the model change
        session.add(
            AuditLog(
                event_type="models.changed",
                actor=actor,
                context=json.dumps(model_selection_data, ensure_ascii=False),
                created_at=utc_now(),
            )
        )
        session.commit()

        # Trigger re-embedding operation to process all content with new models
        try:
            operations_service.trigger(job_type="reembed", payload=model_selection_data, actor=actor)
            message = (
                f"Model selection saved. Server restart required to load new models. "
                f"All content will be re-embedded automatically after restart."
            )
        except Exception as reembed_exc:
            logger.warning(f"Failed to trigger reembed job: {reembed_exc}")
            message = (
                f"Model selection saved. Server restart required to load new models. "
                f"Re-embedding must be triggered manually via sync operation."
            )

        return _operations_partial_response(
            "partials/ops_models_card.html",
            request,
            session,
            settings_service,
            telemetry_service,
            operations_service,
            worker_control,
            message=message,
            message_level="info",
        )

    except Exception as exc:
        logger.exception("Failed to save model selection")
        return _operations_partial_response(
            "partials/ops_models_card.html",
            request,
            session,
            settings_service,
            telemetry_service,
            operations_service,
            worker_control,
            error=f"Failed to save model selection: {str(exc)}",
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
            # Determine target template based on operation type
            target_template = (
                "partials/config_status_card.html"
                if operation_type in ("import", "export")
                else "partials/ops_operations_card.html"
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
            "partials/config_status_card.html"
            if op in {"import", "export", "guidelines"}
            else "partials/ops_operations_card.html"
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
            # Workers card was removed from UI, return JSON error instead
            raise HTTPException(status_code=400, detail=f"Unsupported worker action '{action}'")
    except WorkerUnavailableError as exc:
        # Workers card was removed from UI, return JSON error instead
        raise HTTPException(status_code=503, detail=str(exc))

    # Workers card was removed from UI, return JSON success instead
    return JSONResponse(content={
        "success": True,
        "message": message,
        "action": action,
    })
# DEPRECATED (Phase 2): Sheet configuration now read-only via .env
# Keeping endpoint for backward compatibility but it returns an error message
@router.post("/ui/settings/sheets", response_class=HTMLResponse)
async def load_sheets_config(
    request: Request,
    config_path: str = Form(...),
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    return _respond(
        None,
        request,
        session,
        settings_service,
        error="Sheet configuration is now managed via .env file. Please edit .env and restart if needed.",
    )


# DEPRECATED (Phase 2): Model selection will be moved to Operations page in Phase 3
# Keeping endpoint functional for now as model management is being redesigned
@router.post("/ui/settings/models", response_class=HTMLResponse)
async def update_model_preferences(
    request: Request,
    embedding_choice: Optional[str] = Form(None),
    embedding_repo: Optional[str] = Form(None),
    embedding_quant: Optional[str] = Form(None),
    reranker_choice: Optional[str] = Form(None),
    reranker_repo: Optional[str] = Form(None),
    reranker_quant: Optional[str] = Form(None),
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    actor = _actor_from_request(request)

    selected_embedding_repo, selected_embedding_quant = _parse_model_choice(embedding_choice)
    selected_reranker_repo, selected_reranker_quant = _parse_model_choice(reranker_choice)

    if not selected_embedding_repo:
        selected_embedding_repo = embedding_repo
    if not selected_embedding_quant:
        selected_embedding_quant = embedding_quant
    if not selected_reranker_repo:
        selected_reranker_repo = reranker_repo
    if not selected_reranker_quant:
        selected_reranker_quant = reranker_quant

    if not selected_embedding_repo or not selected_embedding_quant:
        default_embed = EMBEDDING_CHOICES[0]
        selected_embedding_repo = default_embed["repo"]
        selected_embedding_quant = default_embed["quant"]

    if not selected_reranker_repo or not selected_reranker_quant:
        default_reranker = RERANKER_CHOICES[0]
        selected_reranker_repo = default_reranker["repo"]
        selected_reranker_quant = default_reranker["quant"]

    settings_service.update_models(
        session,
        embedding_repo=selected_embedding_repo,
        embedding_quant=selected_embedding_quant,
        reranker_repo=selected_reranker_repo,
        reranker_quant=selected_reranker_quant,
        actor=actor,
    )

    # Invalidate MCP categories cache
    _invalidate_categories_cache_safe()

    auto_msg = ""
    # Automatically trigger re-embedding workflow; OperationsService adapter handles mode gating
    try:
        from src.api.dependencies import get_operations_service

        ops = get_operations_service()
        if ops is not None:
            try:
                ops.trigger("reembed", payload={}, actor=actor)
                auto_msg = " Re-embed job requested."
            except Exception:
                auto_msg = ""
    except Exception:
        auto_msg = ""

    return _respond(
        None,
        request,
        session,
        settings_service,
        message=f"Model preferences updated.{auto_msg}",
        message_level="success",
        trigger_event="settings-changed",
    )


@router.post("/ui/settings/test-connection", response_class=HTMLResponse)
async def test_connection(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    """Test Google Sheets connection using credentials from .env."""
    import os
    from pathlib import Path

    credentials_path = os.getenv("GOOGLE_CREDENTIAL_PATH", "")
    if not credentials_path:
        return _respond(
            "partials/config_status_card.html",
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
        return _respond(
            "partials/config_status_card.html",
            request,
            session,
            settings_service,
            error=f"Credential file not found: {cred_file}",
        )

    # Test connection by attempting to create sheets client
    try:
        from src.storage.sheets_client import SheetsClient
        sheets = SheetsClient(str(cred_file))
        # If we got this far, credentials are valid
        return _respond(
            "partials/config_status_card.html",
            request,
            session,
            settings_service,
            message="Connection test successful. Credentials are valid.",
            message_level="success",
        )
    except Exception as exc:
        return _respond(
            "partials/config_status_card.html",
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
    return _render_partial("partials/diagnostics_panel.html", request, session, settings_service)


@router.post("/ui/settings/diagnostics", response_class=HTMLResponse)
async def diagnostics_probe(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    """Refresh diagnostics by re-running all checks.

    Configuration is now managed via .env file, so this endpoint
    just triggers a fresh diagnostic check without loading any config files.
    """
    # Diagnostics are automatically refreshed when we render the response
    # No need to load scripts_config.yaml since configuration is in .env
    return _respond(
        "partials/diagnostics_panel.html",
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
                return _respond(
                    "partials/backup_card.html",
                    request,
                    session,
                    settings_service,
                    error=f"Sheet config restore failed: {exc}",
                )
        elif any(key in sheets for key in ("spreadsheet_id", "experiences_tab", "manuals_tab", "categories_tab")):
            return _respond(
                "partials/backup_card.html",
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
        # Index card was removed from UI, return JSON error instead
        raise HTTPException(status_code=400, detail=str(exc))

    if archive_path and archive_path.exists():
        archive_path.unlink(missing_ok=True)

    # Best-effort audit log; do not fail the request on logging errors
    try:
        session.add(
            AuditLog(
                event_type="index.snapshot.uploaded",
                actor=actor,
                context=json.dumps(restore_result, ensure_ascii=False),
                created_at=utc_now(),
            )
        )
        # Force flush now to avoid autoflush surprises during template rendering
        session.flush()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to record audit log for snapshot upload: %s", exc, exc_info=True)

    reload_note = "Reloaded index." if restore_result.get("reloaded") else "Restart service to apply changes."
    message = f"Uploaded snapshot ({len(restore_result.get('copied', []))} files). {reload_note}"
    # Index card was removed from UI, return JSON success instead
    return JSONResponse(content={
        "success": True,
        "message": message,
        "files_copied": restore_result.get("copied", []),
        "reloaded": restore_result.get("reloaded", False),
    })


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
