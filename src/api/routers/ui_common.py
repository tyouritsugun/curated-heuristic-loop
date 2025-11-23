"""Shared helpers used by CPU and GPU UI routers."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from src.api.services.settings_service import SettingsService
from src.common.storage.repository import AuditLogRepository
from src.common.storage.schema import AuditLog, utc_now

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

MAX_INDEX_ARCHIVE_BYTES = 512 * 1024 * 1024  # 512 MiB
WEB_ACTOR = "web-ui"
ALLOWED_INDEX_FILE_SUFFIXES = frozenset([".index", ".json", ".backup"])

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


def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request", "").lower() == "true"


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


def _normalize_diagnostics(payload: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Convert diagnostics payload into name-indexed mapping for templates."""
    if not payload:
        return {}
    if isinstance(payload, dict):
        sections = payload.get("sections")
        if isinstance(sections, list):
            ordered: Dict[str, Dict[str, Any]] = {}
            for section in sections:
                if isinstance(section, dict):
                    name = section.get("name")
                    if name:
                        ordered[name] = section
            if ordered:
                return ordered
        return {key: value for key, value in payload.items() if isinstance(value, dict)}
    return {}


def _summarize_job_runs(jobs, last_runs_by_type) -> dict:
    """Convert job runs into a dictionary of summaries indexed by job type.

    Returns a dict like: {'import': {...}, 'export': {...}, 'guidelines': {...}}
    where each value contains summary info about that job type.
    """
    last_runs_by_type = last_runs_by_type or {}
    summaries_by_type = {}

    # Process recent jobs list first
    for job in jobs:
        # jobs are dictionaries from _serialize_job
        job_type = job.get("job_type")
        job_status = job.get("status")
        error_detail = job.get("error", "")
        result = job.get("result", {})
        result_text = result.get("raw") if isinstance(result, dict) and "raw" in result else str(result) if result else None

        summary = {
            "id": job.get("job_id"),
            "type": job_type,
            "status": job_status,
            "status_class": _job_status_class(job_status),
            "status_label": job_status.title() if job_status else "Unknown",
            "pill_class": _job_status_class(job_status),
            "created_at": _format_timestamp(job.get("created_at")),
            "updated_at": _format_timestamp(job.get("finished_at") or job.get("started_at") or job.get("created_at")),
            "duration": _format_duration(job.get("created_at"), job.get("finished_at") or job.get("started_at")),
            "description": error_detail if job_status == "failed" else result_text,
            "detail": error_detail if job_status == "failed" else result_text,
            "is_active": job_status in ("running", "queued"),
        }

        # Only store the most recent job for each type
        if job_type and job_type not in summaries_by_type:
            summaries_by_type[job_type] = summary

    # Fill in any job types from last_runs_by_type that weren't in recent jobs
    for job_type, run in last_runs_by_type.items():
        if job_type in summaries_by_type:
            # Already have a more recent entry
            continue

        # run is also a dictionary from _serialize_job
        if run:
            run_status = run.get("status")
            error_detail = run.get("error", "")
            result = run.get("result", {})
            result_text = result.get("raw") if isinstance(result, dict) and "raw" in result else str(result) if result else None
        else:
            run_status = "never"
            error_detail = None
            result_text = None

        summaries_by_type[job_type] = {
            "id": run.get("job_id") if run else None,
            "type": job_type,
            "status": run_status,
            "status_class": _job_status_class(run_status) if run else "warn",
            "status_label": run_status.title() if run_status else "Never",
            "pill_class": _job_status_class(run_status) if run else "warn",
            "created_at": _format_timestamp(run.get("created_at")) if run else None,
            "updated_at": _format_timestamp(run.get("finished_at") or run.get("started_at") or run.get("created_at")) if run else None,
            "duration": _format_duration(run.get("created_at"), run.get("finished_at") or run.get("started_at")) if run else None,
            "description": error_detail if run_status == "failed" else result_text,
            "detail": error_detail if run_status == "failed" else result_text,
            "is_active": False,
        }

    return summaries_by_type


def _build_index_info(search_service, config) -> dict:
    info = {
        "state": "info",
        "message": "FAISS status unavailable in CPU mode",
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
    except Exception as exc:  # pragma: no cover
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
    except Exception as exc:  # pragma: no cover
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
    config_obj = getattr(request.app.state, "config", None)
    search_mode = getattr(config_obj, "backend", os.getenv("CHL_BACKEND", "cpu"))
    snapshot = settings_service.snapshot(session)
    diagnostics_payload = settings_service.diagnostics(session)
    diagnostics = _normalize_diagnostics(diagnostics_payload)
    context = {
        "request": request,
        "snapshot": snapshot,
        "message": message,
        "message_level": message_level,
        "error": error,
        "secrets_root": str(settings_service.secrets_root),
        "diagnostics": diagnostics,
        "audit_entries": _recent_audit_entries(session),
        "active_page": "settings",
        "default_scripts_config_path": str(_default_scripts_config_path()),
        "embedding_choices": EMBEDDING_CHOICES,
        "reranker_choices": RERANKER_CHOICES,
        "env_config_status": _get_env_config_status(snapshot=snapshot, diagnostics=diagnostics),
        "search_mode": search_mode,
    }
    return context


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
    telemetry_snapshot = telemetry_service.snapshot(session)
    worker_status = worker_control.status(session)
    diagnostics_payload = settings_service.diagnostics(session)
    diagnostics = _normalize_diagnostics(diagnostics_payload)
    jobs = operations_service.list_recent(session, limit=jobs_limit)
    job_summaries = _summarize_job_runs(jobs, operations_service.last_runs_by_type(session))
    index_info = _build_index_info(search_service, config) if (config or search_service) else None
    return {
        "snapshot": telemetry_snapshot,
        "worker_status": worker_status,
        "jobs": jobs,
        "diagnostics": diagnostics,
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
    if template_name == "common/partials/config_status_card.html":
        context = _build_settings_context(
            request,
            session,
            settings_service,
            message=message,
            message_level=message_level,
            error=error,
        )
        jobs = operations_service.list_recent(session, limit=10)
        job_summaries = _summarize_job_runs(jobs, operations_service.last_runs_by_type(session))
        context["job_summaries"] = job_summaries
        context["is_partial"] = True
        response = templates.TemplateResponse(template_name, context)
    else:
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

            if all(
                [
                    model_info["embedding_repo"],
                    model_info["embedding_quant"],
                    model_info["reranker_repo"],
                    model_info["reranker_quant"],
                ]
            ):
                model_info["state"] = "ok"
                model_info["headline"] = "Loaded"
        except (json.JSONDecodeError, OSError):
            pass

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
    import os
    from pathlib import Path

    diagnostics = diagnostics or {}
    snapshot = snapshot or {}

    credentials_path = os.getenv("GOOGLE_CREDENTIAL_PATH", "")
    import_sheet_id = os.getenv("IMPORT_SPREADSHEET_ID", "")
    export_sheet_id = os.getenv("EXPORT_SPREADSHEET_ID", "")

    import_worksheets = ", ".join(
        [
            os.getenv("IMPORT_WORKSHEET_CATEGORIES", "Categories"),
            os.getenv("IMPORT_WORKSHEET_EXPERIENCES", "Experiences"),
            os.getenv("IMPORT_WORKSHEET_MANUALS", "Manuals"),
        ]
    )

    export_worksheets = ", ".join(
        [
            os.getenv("EXPORT_WORKSHEET_CATEGORIES", "Categories"),
            os.getenv("EXPORT_WORKSHEET_EXPERIENCES", "Experiences"),
            os.getenv("EXPORT_WORKSHEET_MANUALS", "Manuals"),
        ]
    )

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
            project_root = Path(__file__).resolve().parents[3]
            cred_file = (project_root / cred_file).resolve()

        if cred_file.exists():
            if cred_file.is_file():
                try:
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

    sheets_state = "warn"
    sheets_status = "Not configured"
    sheets_detail = None

    if import_sheet_id and export_sheet_id:
        sheets_state = "ok"
        sheets_status = "Connected"
    elif import_sheet_id or export_sheet_id:
        sheets_state = "warn"
        sheets_status = "Partially configured"

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


def _index_dir(config) -> Optional[Path]:
    path = getattr(config, "faiss_index_path", None)
    if not path:
        return None
    return Path(path)


def _create_index_archive(config) -> Path:
    index_dir = _index_dir(config)
    if not index_dir or not index_dir.exists():
        raise IndexUploadError("FAISS index directory not found.")

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    tmp.close()
    archive_path = Path(tmp.name)

    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in index_dir.glob("*"):
            if file_path.is_file() and file_path.suffix.lower() in ALLOWED_INDEX_FILE_SUFFIXES:
                archive.write(file_path, arcname=file_path.name)

    if archive_path.stat().st_size <= 0:
        archive_path.unlink(missing_ok=True)
        raise IndexUploadError("Generated archive was empty.")
    return archive_path


async def _persist_archive_upload(upload_file) -> Path:
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
    except Exception:  # pragma: no cover
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
    except Exception as exc:  # pragma: no cover
        logger.warning("Failed to hot-reload index: %s", exc, exc_info=True)
        return False


def _validate_index_member(member: zipfile.ZipInfo) -> str:
    rel_name = Path(member.filename).name
    if not rel_name:
        raise IndexUploadError("Archive contains empty filename.")

    rel_lower = rel_name.lower()
    if not any(rel_lower.endswith(s) for s in ALLOWED_INDEX_FILE_SUFFIXES):
        raise IndexUploadError(f"Unsupported file '{rel_name}'.")

    if member.file_size > MAX_INDEX_ARCHIVE_BYTES:
        raise IndexUploadError(f"File too large: {rel_name}")

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
            validated_names = [_validate_index_member(m) for m in members]
            temp_dir = Path(tempfile.mkdtemp())
            for member, rel_name in zip(members, validated_names):
                dest = (temp_dir / rel_name).resolve()
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


__all__ = [
    "ALLOWED_INDEX_FILE_SUFFIXES",
    "EMBEDDING_CHOICES",
    "IndexUploadError",
    "MAX_INDEX_ARCHIVE_BYTES",
    "RERANKER_CHOICES",
    "TEMPLATES_DIR",
    "WEB_ACTOR",
    "_actor_from_request",
    "_build_operations_context",
    "_build_settings_context",
    "_create_index_archive",
    "_default_scripts_config_path",
    "_get_env_config_status",
    "_get_model_info",
    "_index_dir",
    "_is_htmx",
    "_operations_partial_response",
    "_parse_model_choice",
    "_persist_archive_upload",
    "_recent_audit_entries",
    "_render_operation_result",
    "_render_operations_template",
    "_restore_index_archive",
    "_summarize_job_runs",
    "templates",
]
