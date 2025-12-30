"""Settings service that keeps metadata in SQLite while secrets stay on disk."""
from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from src.common.storage.schema import Setting, AuditLog, utc_now
import yaml


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
    """Structured view of sheet configuration loaded from YAML."""

    config_path: str
    config_checksum: Optional[str]
    data_path: Optional[str]
    google_credentials_path: Optional[str]
    category_sheet_id: str
    category_worksheet: str
    experiences_sheet_id: str
    experiences_worksheet: str
    skills_sheet_id: str
    skills_worksheet: str
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

    def __init__(self, session_factory, secrets_root: str, mode_runtime: Optional[Any] = None):
        self._session_factory = session_factory
        self._secrets_root = Path(secrets_root).resolve()
        self._mode_runtime = mode_runtime
        self._config: Optional[Any] = None

    @property
    def secrets_root(self) -> Path:
        """Return the managed secrets root directory used for relative paths."""
        return self._secrets_root

    def set_mode_runtime(self, mode_runtime: Any) -> None:
        """Attach the active ModeRuntime after server startup."""
        self._mode_runtime = mode_runtime

    def set_config(self, config: Any) -> None:
        """Attach Config object for mode-aware snapshots."""
        self._config = config

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

        mode_payload = None
        if self._config is not None:
            mode_payload = {"search_mode": getattr(self._config, "search_mode", None)}

        return {
            "credentials": credentials.__dict__ if credentials else None,
            "sheets": sheets.__dict__ if sheets else None,
            "models": models.__dict__ if models else None,
            "updated_at": updated_at,
            "mode": mode_payload,
        }

    def update_credentials(self, session: Session, *, path: str, notes: Optional[str], actor: Optional[str]) -> CredentialSettings:
        """Persist credential metadata after validating the file path.

        DEPRECATED: Credentials should be configured via .env file (GOOGLE_CREDENTIAL_PATH).
        This method is retained for backward compatibility with backup/restore functionality.
        """
        resolved = self._resolve_secret_path(path)
        if not resolved.exists() or not resolved.is_file():
            raise SettingValidationError(f"Credential file does not exist: {resolved}")

        # Enforce that credentials live under managed dir
        managed_dir = self._managed_credentials_dir()
        try:
            resolved.relative_to(managed_dir)
        except ValueError:
            raise SettingValidationError(
                f"Credentials must live under {managed_dir}. Move the file there before registering it."
            )

        # Enforce strict permissions: reject world/group-readable credentials
        try:
            import stat as _stat
            perms = _stat.S_IMODE(resolved.stat().st_mode)
        except OSError:
            perms = None
        if perms is not None and (perms & 0o077):
            raise SettingValidationError(
                f"Credential permissions too open ({oct(perms)}). Fix with: chmod 600 {resolved}"
            )

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

    def load_sheet_config(
        self,
        session: Session,
        *,
        config_path: str,
        actor: Optional[str],
    ) -> SheetSettings:
        """Load Google Sheets metadata from a YAML config file.

        DEPRECATED: Sheet configuration should be managed via .env file:
        - GOOGLE_CREDENTIAL_PATH for credentials
        - IMPORT_SPREADSHEET_ID and EXPORT_SPREADSHEET_ID for sheet IDs
        - IMPORT_WORKSHEET_* and EXPORT_WORKSHEET_* for worksheet names (optional)

        This method is retained for backward compatibility with existing deployments
        and the diagnostics probe functionality.
        """
        resolved = Path(config_path).expanduser().resolve()
        if not resolved.exists() or not resolved.is_file():
            raise SettingValidationError(f"Config file does not exist: {resolved}")

        def _resolve_relative(value: str | Path) -> Path:
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = (resolved.parent / candidate).resolve()
            else:
                candidate = candidate.resolve()
            return candidate

        try:
            raw = resolved.read_text("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SettingValidationError(f"Unable to read config file: {exc}") from exc

        try:
            data = yaml.safe_load(raw) or {}
        except yaml.YAMLError as exc:
            raise SettingValidationError(f"Config file contains invalid YAML: {exc}") from exc

        if not isinstance(data, dict):
            raise SettingValidationError("Config file must define a mapping at the top level.")

        export_cfg = data.get("export")
        if not isinstance(export_cfg, dict):
            raise SettingValidationError("Config file missing 'export' section with sheet IDs.")

        import_cfg = data.get("import") if isinstance(data.get("import"), dict) else None

        data_path_value = data.get("data_path")
        if data_path_value:
            data_path = _resolve_relative(data_path_value)
        else:
            data_path = (resolved.parent.parent / "data").resolve()

        credential_path_value = (
            data.get("google_credentials_path")
            or export_cfg.get("google_credentials_path")
            or (import_cfg.get("google_credentials_path") if import_cfg else None)
        )
        if not credential_path_value:
            raise SettingValidationError(
                "scripts_config.yaml must define google_credentials_path (top-level or under export/import)."
            )
        google_credentials_path = _resolve_relative(credential_path_value)
        managed_dir = self._managed_credentials_dir()
        try:
            google_credentials_path.relative_to(managed_dir)
        except ValueError:
            raise SettingValidationError(
                "scripts_config.yaml points to credentials at "
                f"{google_credentials_path}, but managed credentials must live under {managed_dir}. "
                "Move or copy the JSON into that directory (or set CHL_EXPERIENCE_ROOT to match) "
                "before running Load & Verify."
            )

        spreadsheet_id = (export_cfg.get("spreadsheet_id") or export_cfg.get("sheet_id") or "").strip()
        worksheets_cfg = export_cfg.get("worksheets") if isinstance(export_cfg.get("worksheets"), dict) else None

        def _extract(section_key: str, legacy_key: str, default_ws: str) -> Dict[str, str]:
            worksheet = None
            sheet_id = None

            entry = None
            if worksheets_cfg:
                entry = worksheets_cfg.get(section_key)
                if entry is None and section_key.endswith("ies"):
                    entry = worksheets_cfg.get(section_key[:-3] + "y")
                if entry is None and section_key.endswith("s"):
                    entry = worksheets_cfg.get(section_key[:-1])

            if isinstance(entry, str):
                worksheet = entry.strip() or default_ws
            elif isinstance(entry, dict):
                worksheet = (entry.get("worksheet") or entry.get("name") or default_ws).strip()
                sheet_id = (entry.get("sheet_id") or entry.get("id") or "").strip()

            if sheet_id:
                return {"sheet_id": sheet_id, "worksheet": worksheet or default_ws}

            if not worksheet and worksheets_cfg and legacy_key in worksheets_cfg:
                legacy_entry = worksheets_cfg[legacy_key]
                if isinstance(legacy_entry, str):
                    worksheet = legacy_entry.strip() or default_ws

            return {"sheet_id": spreadsheet_id, "worksheet": worksheet or default_ws}

        categories_cfg = _extract("categories", "category", "Categories")
        experiences_cfg = _extract("experiences", "experience", "Experiences")
        skills_cfg = _extract("skills", "manuals", "Skills")

        payload = {
            "config_path": str(resolved),
            "config_checksum": self._sha256(resolved),
            "data_path": str(data_path),
            "google_credentials_path": str(google_credentials_path),
            "category_sheet_id": categories_cfg["sheet_id"],
            "category_worksheet": categories_cfg["worksheet"],
            "experiences_sheet_id": experiences_cfg["sheet_id"],
            "experiences_worksheet": experiences_cfg["worksheet"],
            "skills_sheet_id": skills_cfg["sheet_id"],
            "skills_worksheet": skills_cfg["worksheet"],
        }

        record = self._upsert_setting(
            session,
            key=self.SHEETS_KEY,
            value=payload,
            checksum=payload["config_checksum"],
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
        """Persist preferred embedding and reranker model configuration."""
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

    def diagnostics(self, session: Session) -> Dict[str, Any]:
        """Return a set of diagnostic statuses for display in the UI."""
        credentials = self._deserialize_credentials(self._get_setting(session, self.CREDENTIALS_KEY))
        sheets = self._deserialize_sheets(self._get_setting(session, self.SHEETS_KEY))
        models = self._deserialize_models(self._get_setting(session, self.MODELS_KEY))

        sections: list[DiagnosticStatus] = []

        sections.append(self._diagnose_credentials(credentials))
        sections.append(self._diagnose_sheets(sheets))

        # Only include models diagnostic in GPU/auto mode
        is_cpu_mode = self._config and getattr(self._config, "search_mode", None) == "cpu"
        if not is_cpu_mode:
            sections.append(self._diagnose_models(models))

        # Attach mode-specific diagnostics if the runtime provides them
        adapter = getattr(self._mode_runtime, "diagnostics_adapter", None) if self._mode_runtime else None
        if adapter and hasattr(adapter, "faiss_status"):
            try:
                faiss_path = Path(
                    getattr(
                        self._config,
                        "faiss_index_path",
                        self._secrets_root,
                    )
                )
                status = adapter.faiss_status(faiss_path, session)
                sections.append(
                    DiagnosticStatus(
                        name="runtime.faiss",
                        state=status.get("state", "info"),
                        headline=status.get("headline", "Runtime diagnostics"),
                        detail=status.get("detail"),
                        validated_at=status.get("validated_at"),
                    )
                )
            except Exception as exc:  # pragma: no cover - defensive
                sections.append(
                    DiagnosticStatus(
                        name="runtime.error",
                        state="error",
                        headline="Runtime diagnostics failed",
                        detail=str(exc),
                        validated_at=None,
                    )
                )

        return {
            "sections": [s.to_dict() for s in sections],
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _sha256(self, path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def _resolve_secret_path(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = (self._secrets_root / candidate).resolve()
        return candidate

    def _managed_credentials_dir(self) -> Path:
        # For now, managed credentials live under experience_root/credentials
        return (self._secrets_root / "credentials").resolve()

    def _get_setting(self, session: Session, key: str) -> Optional[Setting]:
        return (
            session.query(Setting)
            .filter(Setting.key == key)
            .order_by(Setting.updated_at.desc())
            .first()
        )

    def _upsert_setting(
        self,
        session: Session,
        *,
        key: str,
        value: Dict[str, Any],
        checksum: Optional[str],
        notes: Optional[str],
    ) -> Setting:
        record = self._get_setting(session, key)
        serialized = json.dumps(value, ensure_ascii=False)
        if record is None:
            record = Setting(
                key=key,
                value_json=serialized,
                checksum=checksum,
                notes=notes,
                updated_at=utc_now(),
            )
            session.add(record)
        else:
            record.value_json = serialized
            record.checksum = checksum
            record.notes = notes
            record.updated_at = utc_now()
        return record

    def _append_audit(self, session: Session, event_type: str, actor: Optional[str], context: Dict[str, Any]):
        session.add(
            AuditLog(
                event_type=event_type,
                actor=actor,
                context=json.dumps(context, ensure_ascii=False),
                created_at=utc_now(),
            )
        )

    def _deserialize_credentials(self, record: Optional[Setting]) -> Optional[CredentialSettings]:
        if record is None:
            return None
        try:
            payload = json.loads(record.value_json)
        except json.JSONDecodeError:
            return None
        return CredentialSettings(
            path=str(payload.get("path") or ""),
            filename=str(payload.get("filename") or ""),
            checksum=str(payload.get("checksum") or ""),
            size_bytes=int(payload.get("size_bytes") or 0),
            validated_at=record.updated_at,
        )

    def _deserialize_sheets(self, record: Optional[Setting]) -> Optional[SheetSettings]:
        if record is None:
            return None
        try:
            payload = json.loads(record.value_json)
        except json.JSONDecodeError:
            return None
        skills_sheet_id = payload.get("skills_sheet_id") or payload.get("manuals_sheet_id") or ""
        skills_worksheet = payload.get("skills_worksheet") or payload.get("manuals_worksheet") or ""
        return SheetSettings(
            config_path=str(payload.get("config_path") or ""),
            config_checksum=str(payload.get("config_checksum") or ""),
            data_path=str(payload.get("data_path") or ""),
            google_credentials_path=str(payload.get("google_credentials_path") or ""),
            category_sheet_id=str(payload.get("category_sheet_id") or ""),
            category_worksheet=str(payload.get("category_worksheet") or ""),
            experiences_sheet_id=str(payload.get("experiences_sheet_id") or ""),
            experiences_worksheet=str(payload.get("experiences_worksheet") or ""),
            skills_sheet_id=str(skills_sheet_id),
            skills_worksheet=str(skills_worksheet),
            validated_at=record.updated_at,
        )

    def _deserialize_models(self, record: Optional[Setting]) -> Optional[ModelSettings]:
        if record is None:
            return None
        try:
            payload = json.loads(record.value_json)
        except json.JSONDecodeError:
            return None
        return ModelSettings(
            embedding_repo=payload.get("embedding_repo"),
            embedding_quant=payload.get("embedding_quant"),
            reranker_repo=payload.get("reranker_repo"),
            reranker_quant=payload.get("reranker_quant"),
            validated_at=record.updated_at,
        )

    def _diagnose_credentials(self, settings: Optional[CredentialSettings]) -> DiagnosticStatus:
        # Check environment variable first (preferred for CPU mode)
        import os
        from src.common.config.config import PROJECT_ROOT
        credentials_path = os.getenv("GOOGLE_CREDENTIAL_PATH")
        validated_at = None

        if credentials_path:
            # Using .env configuration - resolve relative to project root
            path = Path(credentials_path)
            if not path.is_absolute():
                path = (PROJECT_ROOT / path).resolve()
            validated_at = utc_now()
        elif settings is not None and settings.path:
            # Fallback to database settings
            path = Path(settings.path)
            validated_at = settings.validated_at
        else:
            return DiagnosticStatus(
                name="credentials",
                state="warn",
                headline="Credentials not configured",
                detail="Set GOOGLE_CREDENTIAL_PATH in .env to configure API access.",
                validated_at=None,
            )

        if not path.exists():
            return DiagnosticStatus(
                name="credentials",
                state="error",
                headline="Credential file missing",
                detail=f"Expected credential file at {path}, but it does not exist.",
                validated_at=validated_at,
            )
        try:
            st = path.stat()
        except OSError as exc:
            return DiagnosticStatus(
                name="credentials",
                state="error",
                headline="Unable to read credential file",
                detail=str(exc),
                validated_at=validated_at,
            )
        if not stat.S_ISREG(st.st_mode):
            return DiagnosticStatus(
                name="credentials",
                state="error",
                headline="Credential path is not a file",
                detail=f"Expected a file at {path}, but found something else.",
                validated_at=validated_at,
            )
        # Basic sanity checks pass
        return DiagnosticStatus(
            name="credentials",
            state="ok",
            headline="Credentials configured",
            detail=None,
            validated_at=validated_at,
        )

    def _diagnose_sheets(self, settings: Optional[SheetSettings]) -> DiagnosticStatus:
        # Check environment variables first (preferred for CPU mode)
        import os
        import_sheet_id = os.getenv("IMPORT_SPREADSHEET_ID", "").strip()
        export_sheet_id = os.getenv("EXPORT_SPREADSHEET_ID", "").strip()
        validated_at = None

        if import_sheet_id or export_sheet_id:
            # Using .env configuration
            validated_at = utc_now()
            if import_sheet_id and export_sheet_id:
                return DiagnosticStatus(
                    name="sheets",
                    state="ok",
                    headline="Sheets configured",
                    detail=None,
                    validated_at=validated_at,
                )
            else:
                missing = []
                if not import_sheet_id:
                    missing.append("IMPORT_SPREADSHEET_ID")
                if not export_sheet_id:
                    missing.append("EXPORT_SPREADSHEET_ID")
                return DiagnosticStatus(
                    name="sheets",
                    state="warn",
                    headline="Sheets partially configured",
                    detail=f"Set {' and '.join(missing)} in .env to complete configuration.",
                    validated_at=validated_at,
                )
        elif settings is not None:
            # Fallback to database settings
            validated_at = settings.validated_at
            missing = []
            if not settings.category_sheet_id:
                missing.append("categories")
            if not settings.experiences_sheet_id:
                missing.append("experiences")
            if not settings.skills_sheet_id:
                missing.append("skills")
            if missing:
                return DiagnosticStatus(
                    name="sheets",
                    state="error",
                    headline="Sheets configuration incomplete",
                    detail=f"Missing sheet IDs for: {', '.join(sorted(missing))}.",
                    validated_at=validated_at,
                )
            return DiagnosticStatus(
                name="sheets",
                state="ok",
                headline="Sheets configured",
                detail=None,
                validated_at=validated_at,
            )
        else:
            return DiagnosticStatus(
                name="sheets",
                state="warn",
                headline="Sheets not configured",
                detail=(
                    "Set IMPORT_SPREADSHEET_ID/EXPORT_SPREADSHEET_ID in .env or run the "
                    "Load & Verify wizard to configure Sheets."
                ),
                validated_at=None,
            )

    def _diagnose_models(self, settings: Optional[ModelSettings]) -> DiagnosticStatus:
        # Prefer explicit DB-backed settings, but fall back to Config defaults
        # when available so that environments configured via scripts/setup/check_api_env.py
        # and scripts/setup/setup-gpu.py are treated as "configured".
        cfg = self._config
        effective_embedding_repo = settings.embedding_repo if settings and settings.embedding_repo else getattr(
            cfg, "embedding_repo", None
        )
        effective_embedding_quant = settings.embedding_quant if settings and settings.embedding_quant else getattr(
            cfg, "embedding_quant", None
        )
        effective_reranker_repo = settings.reranker_repo if settings and settings.reranker_repo else getattr(
            cfg, "reranker_repo", None
        )
        effective_reranker_quant = settings.reranker_quant if settings and settings.reranker_quant else getattr(
            cfg, "reranker_quant", None
        )

        if not any(
            [effective_embedding_repo, effective_embedding_quant, effective_reranker_repo, effective_reranker_quant]
        ):
            return DiagnosticStatus(
                name="models",
                state="warn",
                headline="Models not configured",
                detail=(
                    "Run scripts/setup/check_api_env.py and scripts/setup/setup-gpu.py to record model preferences, "
                    "or set CHL_EMBEDDING_REPO/CHL_RERANKER_REPO in .env."
                ),
                validated_at=None,
            )

        problems = []
        if not effective_embedding_repo:
            problems.append("embedding model")
        if not effective_reranker_repo:
            problems.append("reranker model")
        if problems:
            return DiagnosticStatus(
                name="models",
                state="warn",
                headline="Model preferences incomplete",
                detail=f"Missing configuration for: {', '.join(sorted(problems))}.",
                validated_at=settings.validated_at if settings else None,
            )
        return DiagnosticStatus(
            name="models",
            state="ok",
            headline="Models configured",
            detail=None,
            validated_at=settings.validated_at if settings else None,
        )


__all__ = [
    "SettingsService",
    "SettingValidationError",
    "CredentialSettings",
    "SheetSettings",
    "ModelSettings",
    "DiagnosticStatus",
]
