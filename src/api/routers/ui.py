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


TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "web" / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

MAX_UPLOAD_BYTES = 512 * 1024  # 512 KiB upper bound for credential JSON
WEB_ACTOR = "web-ui"


class CredentialUploadError(Exception):
    """Raised when a credential upload fails validation."""


router = APIRouter(tags=["ui"])


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


def _render_settings_page(
    request: Request,
    session: Session,
    settings_service: SettingsService,
    *,
    message: Optional[str] = None,
    message_level: str = "info",
    error: Optional[str] = None,
):
    snapshot = settings_service.snapshot(session)
    context = {
        "request": request,
        "snapshot": snapshot,
        "message": message,
        "message_level": message_level,
        "error": error,
        "managed_credentials_dir": str(_managed_credentials_dir(settings_service)),
        "secrets_root": str(settings_service.secrets_root),
    }
    return templates.TemplateResponse(request, "settings.html", context)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    """Render the settings dashboard."""
    return _render_settings_page(request, session, settings_service)


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
        return _render_settings_page(request, session, settings_service, error=str(exc))

    return _render_settings_page(
        request,
        session,
        settings_service,
        message="Credentials path saved and validated.",
        message_level="success",
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
        return _render_settings_page(request, session, settings_service, error=str(exc))

    return _render_settings_page(
        request,
        session,
        settings_service,
        message=f"Credential uploaded to {saved_path}.",
        message_level="success",
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
        return _render_settings_page(request, session, settings_service, error=str(exc))

    return _render_settings_page(
        request,
        session,
        settings_service,
        message="Sheet configuration saved.",
        message_level="success",
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

    return _render_settings_page(
        request,
        session,
        settings_service,
        message="Model preferences updated.",
        message_level="success",
    )


@router.post("/ui/settings/credentials/test", response_class=HTMLResponse)
async def revalidate_credentials(
    request: Request,
    session: Session = Depends(get_db_session),
    settings_service: SettingsService = Depends(get_settings_service),
):
    actor = _actor_from_request(request)
    snapshot = settings_service.snapshot(session)
    credentials = snapshot.get("credentials") if snapshot else None
    if not credentials or not credentials.get("path"):
        return _render_settings_page(request, session, settings_service, error="No credentials configured to validate.")

    try:
        settings_service.update_credentials(
            session,
            path=credentials["path"],
            notes=None,
            actor=actor,
        )
    except SettingValidationError as exc:
        return _render_settings_page(request, session, settings_service, error=str(exc))

    return _render_settings_page(
        request,
        session,
        settings_service,
        message="Credentials revalidated successfully.",
        message_level="success",
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
