"""Server-rendered UI endpoints for settings management."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from src.api.dependencies import get_db_session, get_settings_service
from src.services.settings_service import SettingValidationError, SettingsService
from src.storage.repository import AuditLogRepository


TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "web" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

MAX_UPLOAD_BYTES = 512 * 1024  # 512 KiB upper bound for credential JSON
WEB_ACTOR = "web-ui"


class CredentialUploadError(Exception):
    """Raised when a credential upload fails validation."""


router = APIRouter(tags=["ui"])


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


def _build_context(
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
    context = _build_context(
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
    context = _build_context(
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
    return _respond(
        "partials/backup_card.html",
        request,
        session,
        settings_service,
        message=f"Restored sections: {applied_msg}.",
        message_level="success",
        trigger_event="settings-changed",
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
