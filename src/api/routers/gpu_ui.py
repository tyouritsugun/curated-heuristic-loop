"""GPU-specific UI router."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
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
from src.api.services import gpu_installer
from src.api.services.operations_service import OperationConflict, JobNotFoundError
from src.api.services.settings_service import SettingValidationError, SettingsService
from src.api.services.worker_control import WorkerUnavailableError
from src.common.storage.schema import AuditLog, utc_now

from .ui_common import (
    EMBEDDING_CHOICES,
    IndexUploadError,
    RERANKER_CHOICES,
    templates,
    _actor_from_request,
    _build_operations_context,
    _build_settings_context,
    _create_index_archive,
    _operations_partial_response,
    _parse_model_choice,
    _persist_archive_upload,
    _render_operation_result,
    _restore_index_archive,
    _summarize_job_runs,
    _get_model_info,
    _is_htmx,
)

router = APIRouter(tags=["ui"])

GPU_SETTINGS_TEMPLATE = "gpu/settings_gpu.html"
GPU_OPERATIONS_TEMPLATE = "gpu/operations_gpu.html"


def _json_serialize_datetime(obj):
    """Convert datetime objects to ISO format strings for JSON serialization."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _gpu_features_enabled(*, search_mode: Optional[str] = None) -> bool:
    mode = search_mode or os.getenv("CHL_SEARCH_MODE", "auto")
    return (mode or "auto").lower() != "cpu"


def _build_gpu_runtime_context(*, search_mode: Optional[str] = None) -> dict:
    active_mode = (search_mode or os.getenv("CHL_SEARCH_MODE", "auto") or "auto").lower()
    priority_raw = os.getenv("CHL_GPU_PRIORITY")
    priority = gpu_installer.parse_gpu_priority(priority_raw)
    backend_override = os.getenv("CHL_GPU_BACKEND")
    if active_mode == "cpu":
        return {
            "gpu_state": None,
            "gpu_priority": priority,
            "gpu_priority_raw": priority_raw,
            "gpu_recommended_suffix": None,
            "gpu_install_supported": False,
            "gpu_backend_override": backend_override,
            "gpu_prereq": {
                "status": "disabled",
                "message": "GPU runtime disabled while CHL_SEARCH_MODE=cpu.",
            },
        }

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
        "gpu_backend_override": backend_override,
        "gpu_prereq": prereq,
    }


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
    return templates.TemplateResponse("gpu/partials/settings_gpu_runtime.html", context)


def _render_gpu_full(
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
    search_mode = context.get("search_mode")
    if _gpu_features_enabled(search_mode=search_mode):
        context.update(_build_gpu_runtime_context(search_mode=search_mode))
    context["is_partial"] = False
    return templates.TemplateResponse(GPU_SETTINGS_TEMPLATE, context)


def _render_gpu_partial(
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
    search_mode = context.get("search_mode")
    if _gpu_features_enabled(search_mode=search_mode):
        context.update(_build_gpu_runtime_context(search_mode=search_mode))
    context["is_partial"] = True
    return templates.TemplateResponse(template_name, context)


def _gpu_respond(
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
        response = _render_gpu_partial(
            template_name,
            request,
            session,
            settings_service,
            message=message,
            message_level=message_level,
            error=error,
        )
    else:
        response = _render_gpu_full(
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


def _check_disk_space(min_gb: float = 10.0) -> tuple[bool, str]:
    try:
        home = Path.home()
        stat = shutil.disk_usage(home)
        available_gb = stat.free / (1024 ** 3)
        if available_gb < min_gb:
            return False, f"Insufficient disk space: {available_gb:.1f} GB available, {min_gb:.1f} GB required"
        return True, f"{available_gb:.1f} GB available"
    except Exception as exc:
        return True, f"Could not verify disk space ({exc})"


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
    operations_service=Depends(get_operations_service),
    config=Depends(get_config),
):
    context = _build_settings_context(request, session, settings_service)
    jobs = operations_service.list_recent(session, limit=10)
    job_summaries = _summarize_job_runs(jobs, operations_service.last_runs_by_type(session))
    context["job_summaries"] = job_summaries
    context["is_partial"] = False
    context["search_mode"] = config.search_mode
    if _gpu_features_enabled(search_mode=config.search_mode):
        context.update(_build_gpu_runtime_context(search_mode=config.search_mode))
    return templates.TemplateResponse(GPU_SETTINGS_TEMPLATE, context)


@router.get("/ui/settings/gpu/card", response_class=HTMLResponse)
def settings_gpu_card(request: Request):
    return _render_gpu_card(request)


@router.post("/ui/settings/gpu/detect", response_class=HTMLResponse)
def settings_gpu_detect(request: Request):
    if not _gpu_features_enabled():
        return _render_gpu_card(
            request,
            message="GPU controls are disabled while CHL_SEARCH_MODE=cpu.",
            message_level="warn",
        )
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
        return _render_gpu_card(request, message=str(exc), message_level="error")

    return _render_gpu_card(request, message=message, message_level=level, log=log)


@router.post("/ui/settings/gpu/install", response_class=HTMLResponse)
def settings_gpu_install(request: Request):
    if not _gpu_features_enabled():
        return _render_gpu_card(
            request,
            message="GPU controls are disabled while CHL_SEARCH_MODE=cpu.",
            message_level="warn",
        )
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
    if not _gpu_features_enabled():
        return _render_gpu_card(
            request,
            message="GPU controls are disabled while CHL_SEARCH_MODE=cpu.",
            message_level="warn",
        )
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
            "search_mode": config.search_mode,
        }
    )
    return templates.TemplateResponse(GPU_OPERATIONS_TEMPLATE, context)


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
        "common/partials/ops_models_card.html",
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
    from src.common.storage.repository import ExperienceRepository, ManualRepository

    current_models = _get_model_info()

    exp_repo = ExperienceRepository(session)
    manual_repo = ManualRepository(session)

    experience_count = exp_repo.count()
    manual_count = manual_repo.count()
    total_items = experience_count + manual_count

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

    return templates.TemplateResponse("common/partials/model_change_modal.html", context)


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
    actor = _actor_from_request(request)

    has_space, space_msg = _check_disk_space(min_gb=10.0)
    if not has_space:
        return _operations_partial_response(
            "common/partials/ops_models_card.html",
            request,
            session,
            settings_service,
            telemetry_service,
            operations_service,
            worker_control,
            error=f"Cannot change models: {space_msg}. Free up disk space and try again.",
        )

    embedding_repo, embedding_quant = _parse_model_choice(embedding_choice)
    reranker_repo, reranker_quant = _parse_model_choice(reranker_choice)

    if not all([embedding_repo, embedding_quant, reranker_repo, reranker_quant]):
        return _operations_partial_response(
            "common/partials/ops_models_card.html",
            request,
            session,
            settings_service,
            telemetry_service,
            operations_service,
            worker_control,
            error="Invalid model selection",
        )

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

        session.add(
            AuditLog(
                event_type="models.changed",
                actor=actor,
                context=json.dumps(model_selection_data, ensure_ascii=False),
                created_at=utc_now(),
            )
        )
        session.commit()

        try:
            operations_service.trigger(job_type="reembed", payload=model_selection_data, actor=actor)
            message = (
                "Model selection saved. Server restart required to load new models. "
                "All content will be re-embedded automatically after restart."
            )
        except Exception:
            message = (
                "Model selection saved. Server restart required to load new models. "
                "Re-embedding must be triggered manually via sync operation."
            )

        return _operations_partial_response(
            "common/partials/ops_models_card.html",
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
        return _operations_partial_response(
            "common/partials/ops_models_card.html",
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
    return _gpu_respond(
        None,
        request,
        session,
        settings_service,
        error="Sheet configuration is now managed via .env file. Please edit .env and restart if needed.",
    )


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
    operations_service=Depends(get_operations_service),
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

    auto_msg = ""
    if operations_service is not None:
        try:
            operations_service.trigger("reembed", payload={}, actor=actor)
            auto_msg = " Re-embed job requested."
        except Exception:
            auto_msg = ""

    return _gpu_respond(
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
    credentials_path = os.getenv("GOOGLE_CREDENTIAL_PATH", "")
    if not credentials_path:
        return _gpu_respond(
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
        return _gpu_respond(
            "common/partials/config_status_card.html",
            request,
            session,
            settings_service,
            error=f"Credential file not found: {cred_file}",
        )

    try:
        from src.common.storage.sheets_client import SheetsClient

        SheetsClient(str(cred_file))
        return _gpu_respond(
            "common/partials/config_status_card.html",
            request,
            session,
            settings_service,
            message="Connection test successful. Credentials are valid.",
            message_level="success",
        )
    except Exception as exc:
        return _gpu_respond(
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
    return _render_gpu_partial("common/partials/diagnostics_panel.html", request, session, settings_service)


@router.post("/ui/settings/diagnostics", response_class=HTMLResponse)
async def diagnostics_probe(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    return _gpu_respond(
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
    return _render_gpu_partial("common/partials/audit_log.html", request, session, settings_service)


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
        return _gpu_respond(
            "common/partials/backup_card.html",
            request,
            session,
            settings_service,
            error="Backup JSON payload is required.",
        )

    try:
        payload = json.loads(backup_json)
    except json.JSONDecodeError as exc:
        return _gpu_respond(
            "common/partials/backup_card.html",
            request,
            session,
            settings_service,
            error=f"Invalid JSON: {exc.msg}.",
        )

    if not isinstance(payload, dict):
        return _gpu_respond(
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
            return _gpu_respond(
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
                return _gpu_respond(
                    "common/partials/backup_card.html",
                    request,
                    session,
                    settings_service,
                    error=f"Sheet config restore failed: {exc}",
                )
        elif any(key in sheets for key in ("spreadsheet_id", "experiences_tab", "manuals_tab", "categories_tab")):
            return _gpu_respond(
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
        return _gpu_respond(
            "common/partials/backup_card.html",
            request,
            session,
            settings_service,
            error="No restorable sections found in backup payload.",
        )

    applied_msg = ", ".join(applied)
    return _gpu_respond(
        "common/partials/backup_card.html",
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

    def _cleanup(path: str):
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
        raise HTTPException(status_code=400, detail=str(exc))

    if archive_path and archive_path.exists():
        archive_path.unlink(missing_ok=True)

    try:
        session.add(
            AuditLog(
                event_type="index.snapshot.uploaded",
                actor=actor,
                context=json.dumps(restore_result, ensure_ascii=False),
                created_at=utc_now(),
            )
        )
        session.commit()
    except Exception:
        session.rollback()

    return _render_operation_result(
        "common/partials/ops_operations_card.html",
        request,
        session,
        settings_service,
        telemetry_service,
        operations_service,
        worker_control,
        message="Index snapshot restored from upload.",
        message_level="success",
        trigger_event="operations-updated",
    )
