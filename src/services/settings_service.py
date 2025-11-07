"""Settings service that keeps metadata in SQLite while secrets stay on disk."""
from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from src.storage.schema import Setting, AuditLog, utc_now


class SettingValidationError(Exception):
    """Raised when an invalid setting payload is provided."""


@dataclass
class CredentialSettings:
    """Structured view of credential metadata."""

    path: str
    filename: str
    checksum: str
    size_bytes: int
    validated_at: Optional[str]


@dataclass
class SheetSettings:
    """Structured view of sheet configuration."""

    spreadsheet_id: str
    experiences_tab: str
    manuals_tab: str
    categories_tab: str
    validated_at: Optional[str]


@dataclass
class ModelSettings:
    """Structured view of embedding/reranker model preferences."""

    embedding_repo: Optional[str]
    embedding_quant: Optional[str]
    reranker_repo: Optional[str]
    reranker_quant: Optional[str]
    validated_at: Optional[str]


@dataclass
class DiagnosticStatus:
    """Represents validation state for a settings section."""

    name: str
    state: str  # ok | warn | error
    headline: str
    detail: Optional[str] = None
    validated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "headline": self.headline,
            "detail": self.detail,
            "validated_at": self.validated_at,
        }


class SettingsService:
    """Manages operator-editable settings without persisting raw secrets."""

    CREDENTIALS_KEY = "settings.credentials"
    SHEETS_KEY = "settings.sheets"
    MODELS_KEY = "settings.models"

    def __init__(self, session_factory, secrets_root: str):
        self._session_factory = session_factory
        self._secrets_root = Path(secrets_root).resolve()

    @property
    def secrets_root(self) -> Path:
        """Return the managed secrets root directory used for relative paths."""
        return self._secrets_root

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def snapshot(self, session: Session) -> Dict[str, Any]:
        """Return a consolidated snapshot of settings."""
        credentials = self._deserialize_credentials(self._get_setting(session, self.CREDENTIALS_KEY))
        sheets = self._deserialize_sheets(self._get_setting(session, self.SHEETS_KEY))
        models = self._deserialize_models(self._get_setting(session, self.MODELS_KEY))

        updated_at = None
        for record in (self._get_setting(session, key) for key in (self.CREDENTIALS_KEY, self.SHEETS_KEY, self.MODELS_KEY)):
            if record and (updated_at is None or record.updated_at > updated_at):
                updated_at = record.updated_at

        return {
            "credentials": credentials.__dict__ if credentials else None,
            "sheets": sheets.__dict__ if sheets else None,
            "models": models.__dict__ if models else None,
            "updated_at": updated_at,
        }

    def update_credentials(self, session: Session, *, path: str, notes: Optional[str], actor: Optional[str]) -> CredentialSettings:
        """Persist credential metadata after validating the file path."""
        resolved = self._resolve_secret_path(path)
        if not resolved.exists() or not resolved.is_file():
            raise SettingValidationError(f"Credentials file does not exist: {resolved}")

        checksum = self._sha256(resolved)
        metadata = {
            "path": str(resolved),
            "filename": resolved.name,
            "size_bytes": resolved.stat().st_size,
        }

        record = self._upsert_setting(
            session,
            key=self.CREDENTIALS_KEY,
            value=metadata,
            checksum=checksum,
            notes=notes,
        )
        self._append_audit(session, event_type="settings.credentials.updated", actor=actor, context=metadata)
        return self._deserialize_credentials(record)

    def update_sheets(
        self,
        session: Session,
        *,
        spreadsheet_id: str,
        experiences_tab: str,
        manuals_tab: str,
        categories_tab: str,
        actor: Optional[str],
    ) -> SheetSettings:
        """Persist Google Sheets metadata after lightweight validation."""
        if not spreadsheet_id.strip():
            raise SettingValidationError("spreadsheet_id is required")

        payload = {
            "spreadsheet_id": spreadsheet_id.strip(),
            "experiences_tab": experiences_tab.strip() or "Experiences",
            "manuals_tab": manuals_tab.strip() or "Manuals",
            "categories_tab": categories_tab.strip() or "Categories",
        }

        record = self._upsert_setting(
            session,
            key=self.SHEETS_KEY,
            value=payload,
            checksum=None,
            notes=None,
        )
        self._append_audit(session, event_type="settings.sheets.updated", actor=actor, context=payload)
        return self._deserialize_sheets(record)

    def update_models(
        self,
        session: Session,
        *,
        embedding_repo: Optional[str],
        embedding_quant: Optional[str],
        reranker_repo: Optional[str],
        reranker_quant: Optional[str],
        actor: Optional[str],
    ) -> ModelSettings:
        """Record preferred embedding/reranker models for downstream services."""
        payload = {
            "embedding_repo": (embedding_repo or "").strip() or None,
            "embedding_quant": (embedding_quant or "").strip() or None,
            "reranker_repo": (reranker_repo or "").strip() or None,
            "reranker_quant": (reranker_quant or "").strip() or None,
        }

        record = self._upsert_setting(
            session,
            key=self.MODELS_KEY,
            value=payload,
            checksum=None,
            notes=None,
        )
        self._append_audit(session, event_type="settings.models.updated", actor=actor, context=payload)
        return self._deserialize_models(record)

    def get_setting_value(self, key: str, session: Optional[Session] = None) -> Optional[Dict[str, Any]]:
        """Return a raw dictionary for a setting key."""
        owns_session = False
        if session is None:
            owns_session = True
            session = self._session_factory()
        try:
            row = self._get_setting(session, key)
            return json.loads(row.value_json) if row else None
        finally:
            if owns_session and session is not None:
                session.close()

    def diagnostics(self, session: Session) -> Dict[str, DiagnosticStatus]:
        """Return validation diagnostics for each settings section."""
        snapshot = self.snapshot(session)
        credentials = snapshot.get("credentials")
        sheets = snapshot.get("sheets")
        models = snapshot.get("models")

        cred_status = self._diagnose_credentials(credentials)
        sheets_status = self._diagnose_sheets(sheets, cred_status)
        model_status = self._diagnose_models(models)

        return {
            "credentials": cred_status,
            "sheets": sheets_status,
            "models": model_status,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _resolve_secret_path(self, candidate: str) -> Path:
        candidate_path = Path(candidate).expanduser()
        if not candidate_path.is_absolute():
            candidate_path = (self._secrets_root / candidate_path).resolve()
        else:
            candidate_path = candidate_path.resolve()
        return candidate_path

    def _sha256(self, file_path: Path) -> str:
        digest = hashlib.sha256()
        with file_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(8192), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _get_setting(self, session: Session, key: str) -> Optional[Setting]:
        return session.query(Setting).filter(Setting.key == key).one_or_none()

    def _upsert_setting(
        self,
        session: Session,
        *,
        key: str,
        value: Dict[str, Any],
        checksum: Optional[str],
        notes: Optional[str],
    ) -> Setting:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True)
        now = utc_now()
        record = self._get_setting(session, key)
        if record is None:
            record = Setting(
                key=key,
                value_json=payload,
                checksum=checksum,
                validated_at=now,
                notes=notes,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
        else:
            record.value_json = payload
            record.checksum = checksum
            record.validated_at = now
            record.updated_at = now
            if notes is not None:
                record.notes = notes
        session.flush()
        return record

    def _append_audit(self, session: Session, *, event_type: str, actor: Optional[str], context: Dict[str, Any]):
        entry = AuditLog(event_type=event_type, actor=actor, context=json.dumps(context, ensure_ascii=False), created_at=utc_now())
        session.add(entry)

    def _deserialize_credentials(self, record: Optional[Setting]) -> Optional[CredentialSettings]:
        if not record:
            return None
        data = json.loads(record.value_json)
        return CredentialSettings(
            path=data.get("path", ""),
            filename=data.get("filename", ""),
            checksum=record.checksum or data.get("checksum", ""),
            size_bytes=int(data.get("size_bytes", 0)),
            validated_at=record.validated_at,
        )

    def _deserialize_sheets(self, record: Optional[Setting]) -> Optional[SheetSettings]:
        if not record:
            return None
        data = json.loads(record.value_json)
        return SheetSettings(
            spreadsheet_id=data.get("spreadsheet_id", ""),
            experiences_tab=data.get("experiences_tab", ""),
            manuals_tab=data.get("manuals_tab", ""),
            categories_tab=data.get("categories_tab", ""),
            validated_at=record.validated_at,
        )

    def _deserialize_models(self, record: Optional[Setting]) -> Optional[ModelSettings]:
        if not record:
            return None
        data = json.loads(record.value_json)
        return ModelSettings(
            embedding_repo=data.get("embedding_repo"),
            embedding_quant=data.get("embedding_quant"),
            reranker_repo=data.get("reranker_repo"),
            reranker_quant=data.get("reranker_quant"),
            validated_at=record.validated_at,
        )

    def _diagnose_credentials(self, data: Optional[Dict[str, Any]]) -> DiagnosticStatus:
        if not data:
            return DiagnosticStatus(
                name="credentials",
                state="error",
                headline="Missing credentials",
                detail="Upload the Google service-account JSON or point to an existing readable file.",
            )

        path = Path(data.get("path", "")).expanduser()
        if not path.exists():
            return DiagnosticStatus(
                name="credentials",
                state="error",
                headline="Credential file not found",
                detail=str(path),
                validated_at=data.get("validated_at"),
            )

        if not path.is_file():
            return DiagnosticStatus(
                name="credentials",
                state="error",
                headline="Credential path is not a file",
                detail=str(path),
                validated_at=data.get("validated_at"),
            )

        try:
            raw = path.read_text("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return DiagnosticStatus(
                name="credentials",
                state="error",
                headline="Unable to read credential JSON",
                detail=str(exc),
                validated_at=data.get("validated_at"),
            )

        try:
            json.loads(raw)
        except json.JSONDecodeError as exc:
            return DiagnosticStatus(
                name="credentials",
                state="error",
                headline="Credential JSON is invalid",
                detail=f"Line {exc.lineno}: {exc.msg}",
                validated_at=data.get("validated_at"),
            )

        try:
            mode = path.stat().st_mode
            perms = stat.S_IMODE(mode)
        except OSError:
            perms = None

        if perms is not None and perms & 0o077:
            return DiagnosticStatus(
                name="credentials",
                state="warn",
                headline="Credential readable by other users",
                detail=f"Permissions are {oct(perms)}; recommend 0o600.",
                validated_at=data.get("validated_at"),
            )

        return DiagnosticStatus(
            name="credentials",
            state="ok",
            headline="Credential JSON looks valid",
            detail=f"Stored at {path}",
            validated_at=data.get("validated_at"),
        )

    def _diagnose_sheets(self, data: Optional[Dict[str, Any]], cred_status: DiagnosticStatus) -> DiagnosticStatus:
        if not data:
            return DiagnosticStatus(
                name="sheets",
                state="warn",
                headline="Sheets configuration missing",
                detail="Add spreadsheet ID and worksheet names to sync content.",
            )

        spreadsheet_id = (data.get("spreadsheet_id") or "").strip()
        if not spreadsheet_id:
            return DiagnosticStatus(
                name="sheets",
                state="error",
                headline="Spreadsheet ID required",
                detail="Paste the ID from the Google Sheets URL.",
                validated_at=data.get("validated_at"),
            )

        detail = f"Tabs: {data.get('experiences_tab')}, {data.get('manuals_tab')}, {data.get('categories_tab')}"
        state = "ok"
        headline = "Sheet metadata saved"

        if cred_status.state != "ok":
            state = "warn"
            headline = "Credentials not ready"
            detail = "Sheets will fail until credentials validate."

        return DiagnosticStatus(
            name="sheets",
            state=state,
            headline=headline,
            detail=detail,
            validated_at=data.get("validated_at"),
        )

    def _diagnose_models(self, data: Optional[Dict[str, Any]]) -> DiagnosticStatus:
        if not data:
            return DiagnosticStatus(
                name="models",
                state="warn",
                headline="Using default models",
                detail="Set embedding and reranker repos if you need overrides.",
            )

        embedding_repo = data.get("embedding_repo")
        reranker_repo = data.get("reranker_repo")

        if not embedding_repo and not reranker_repo:
            return DiagnosticStatus(
                name="models",
                state="warn",
                headline="No overrides provided",
                detail="System defaults will be used until you set repos.",
                validated_at=data.get("validated_at"),
            )

        return DiagnosticStatus(
            name="models",
            state="ok",
            headline="Model preferences saved",
            detail=", ".join(filter(None, [embedding_repo, reranker_repo])) or None,
            validated_at=data.get("validated_at"),
        )
