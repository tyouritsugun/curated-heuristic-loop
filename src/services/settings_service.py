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
    manuals_sheet_id: str
    manuals_worksheet: str
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
        """Persist credential metadata after validating the file path.

        DEPRECATED: Credentials should be configured via .env file (GOOGLE_CREDENTIAL_PATH).
        This method is retained for backward compatibility with backup/restore functionality.
        """
        resolved = self._resolve_secret_path(path)
        if not resolved.exists() or not resolved.is_file():
            raise SettingValidationError(f"Credentials file does not exist: {resolved}")

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
                worksheet = (entry.get("worksheet") or entry.get("name") or default_ws).strip() or default_ws
                override = (entry.get("sheet_id") or entry.get("id") or "").strip()
                sheet_id = override or None

            legacy_cfg = export_cfg.get(legacy_key) or {}
            if worksheet is None:
                if isinstance(legacy_cfg, dict):
                    worksheet = (legacy_cfg.get("worksheet") or default_ws).strip() or default_ws
                else:
                    worksheet = default_ws

            if sheet_id is None and isinstance(legacy_cfg, dict):
                legacy_id = (legacy_cfg.get("id") or "").strip()
                if legacy_id:
                    sheet_id = legacy_id

            if sheet_id is None:
                if spreadsheet_id:
                    sheet_id = spreadsheet_id

            if not sheet_id:
                raise SettingValidationError(
                    f"Config file missing a sheet ID for {section_key}. Provide export.spreadsheet_id or set worksheets.{section_key}.sheet_id."
                )

            return {"id": sheet_id, "worksheet": worksheet}

        category_sheet = _extract("categories", "category_sheet", "Categories")
        experiences_sheet = _extract("experiences", "experiences_sheet", "Experiences")
        manuals_sheet = _extract("manuals", "manuals_sheet", "Manuals")

        metadata = {
            "config_path": str(resolved),
            "data_path": str(data_path),
            "google_credentials_path": str(google_credentials_path),
            "category_sheet": category_sheet,
            "experiences_sheet": experiences_sheet,
            "manuals_sheet": manuals_sheet,
        }

        checksum = self._sha256(resolved)
        record = self._upsert_setting(
            session,
            key=self.SHEETS_KEY,
            value=metadata,
            checksum=checksum,
            notes=None,
        )
        self._append_audit(
            session,
            event_type="settings.sheets.config_loaded",
            actor=actor,
            context=metadata,
        )

        # Automatically register credential metadata based on the YAML reference.
        self.update_credentials(
            session,
            path=str(google_credentials_path),
            notes="Registered via scripts_config.yaml",
            actor=actor,
        )

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
        """Return validation diagnostics for each settings section.

        NOTE: This function now primarily checks .env configuration.
        Database-stored settings are deprecated and only checked as fallback.
        """
        import os
        from pathlib import Path

        # Check .env configuration first (Phase 2)
        credentials_path_env = os.getenv("GOOGLE_CREDENTIAL_PATH", "")
        import_sheet_id = os.getenv("IMPORT_SPREADSHEET_ID", "")
        export_sheet_id = os.getenv("EXPORT_SPREADSHEET_ID", "")

        # Build credential diagnostic from .env
        if credentials_path_env:
            cred_file = Path(credentials_path_env)
            if not cred_file.is_absolute():
                project_root = Path(__file__).resolve().parents[2]
                cred_file = (project_root / cred_file).resolve()

            if cred_file.exists() and cred_file.is_file():
                cred_status = DiagnosticStatus(
                    name="credentials",
                    state="ok",
                    headline="Credentials ready",
                    detail=f"Using {cred_file}",
                    validated_at=utc_now(),
                )
            else:
                cred_status = DiagnosticStatus(
                    name="credentials",
                    state="error",
                    headline="Credential file not found",
                    detail=f"Check GOOGLE_CREDENTIAL_PATH in .env: {cred_file}",
                )
        else:
            # Fallback to deprecated database check
            snapshot = self.snapshot(session)
            credentials = snapshot.get("credentials")
            cred_status = self._diagnose_credentials(credentials)

        # Build sheet diagnostic from .env
        if import_sheet_id and export_sheet_id:
            sheets_status = DiagnosticStatus(
                name="sheets",
                state="ok",
                headline="Sheet configuration ready",
                detail=f"Import: {import_sheet_id[:12]}..., Export: {export_sheet_id[:12]}...",
                validated_at=utc_now(),
            )
        else:
            sheets_status = DiagnosticStatus(
                name="sheets",
                state="error",
                headline="Sheet configuration missing",
                detail="Set IMPORT_SPREADSHEET_ID and EXPORT_SPREADSHEET_ID in .env file",
            )

        # Check models from data/model_selection.json
        data_path = self._secrets_root
        model_file = data_path / "model_selection.json"
        if model_file.exists():
            try:
                import json
                with model_file.open("r") as f:
                    model_data = json.load(f)
                embedding = model_data.get("embedding", {})
                reranker = model_data.get("reranker", {})
                model_status = DiagnosticStatus(
                    name="models",
                    state="ok",
                    headline="Model selection configured",
                    detail=f"Embedding: {embedding.get('repo', 'N/A')}, Reranker: {reranker.get('repo', 'N/A')}",
                    validated_at=utc_now(),
                )
            except Exception:
                model_status = DiagnosticStatus(
                    name="models",
                    state="warn",
                    headline="Model file invalid",
                    detail=f"Check {model_file}",
                )
        else:
            model_status = DiagnosticStatus(
                name="models",
                state="info",
                headline="Using default models",
                detail="No custom model selection configured",
            )

        # Check database status
        database_filename = os.getenv("DATABASE_FILENAME", "chl.db")
        db_path = data_path / database_filename
        if db_path.exists():
            try:
                db_size_mb = db_path.stat().st_size / (1024 * 1024)
                # Check if we can query the database
                from src.storage.schema import Experience, CategoryManual
                exp_count = session.query(Experience).count()
                manual_count = session.query(CategoryManual).count()
                database_status = DiagnosticStatus(
                    name="database",
                    state="ok",
                    headline="Database ready",
                    detail=f"{db_size_mb:.1f} MB · {exp_count} experiences · {manual_count} manuals",
                    validated_at=utc_now(),
                )
            except Exception as exc:
                database_status = DiagnosticStatus(
                    name="database",
                    state="error",
                    headline="Database error",
                    detail=str(exc),
                )
        else:
            database_status = DiagnosticStatus(
                name="database",
                state="error",
                headline="Database not found",
                detail=f"Run scripts/setup-gpu.py (GPU mode) or scripts/setup-cpu.py (CPU-only mode) to initialize database at {db_path}",
            )

        # Check FAISS index status (gate in CPU-only mode)
        # If CHL_SEARCH_MODE=sqlite_only, vector stack is intentionally disabled
        search_mode = os.getenv("CHL_SEARCH_MODE", "auto").lower()
        if search_mode == "sqlite_only":
            faiss_status = DiagnosticStatus(
                name="faiss",
                state="info",
                headline="Semantic search disabled",
                detail="CPU-only mode (SQLite keyword search)",
                validated_at=utc_now(),
            )
        else:
            # Index files use model-specific naming: unified_{model_slug}.index
            # Check if any .index file exists in the faiss_index directory
            faiss_index_dir = data_path / "faiss_index"
            index_files = list(faiss_index_dir.glob("*.index")) if faiss_index_dir.exists() else []
            if index_files:
                try:
                    from src.storage.schema import FAISSMetadata
                    from sqlalchemy import func
                    # Count non-deleted vectors in metadata table
                    vector_count = session.query(func.count(FAISSMetadata.id)).filter(
                        FAISSMetadata.deleted == False
                    ).scalar() or 0

                    if vector_count > 0:
                        # Use the first index file found
                        index_size_mb = index_files[0].stat().st_size / (1024 * 1024)
                        # Get most recent creation timestamp
                        latest_entry = session.query(FAISSMetadata).order_by(
                            FAISSMetadata.created_at.desc()
                        ).first()
                        built_date = latest_entry.created_at[:10] if latest_entry and latest_entry.created_at else 'N/A'

                        faiss_status = DiagnosticStatus(
                            name="faiss",
                            state="ok",
                            headline="FAISS index ready",
                            detail=f"{index_size_mb:.1f} MB · {vector_count} vectors · Built {built_date}",
                            validated_at=utc_now(),
                        )
                    else:
                        faiss_status = DiagnosticStatus(
                            name="faiss",
                            state="warn",
                            headline="FAISS metadata missing",
                            detail="Index files exist but metadata table is empty. Rebuild index via Operations page to sync.",
                        )
                except Exception as exc:
                    faiss_status = DiagnosticStatus(
                        name="faiss",
                        state="warn",
                        headline="FAISS check failed",
                        detail=str(exc),
                    )
            else:
                faiss_status = DiagnosticStatus(
                    name="faiss",
                    state="info",
                    headline="FAISS index not built",
                    detail="Build index via Operations page or upload snapshot",
                )

        # Check disk space
        try:
            import shutil
            disk_usage = shutil.disk_usage(str(data_path))
            free_gb = disk_usage.free / (1024 ** 3)
            total_gb = disk_usage.total / (1024 ** 3)
            used_percent = (disk_usage.used / disk_usage.total) * 100

            if used_percent > 90:
                disk_state = "error"
                disk_headline = "Disk space critical"
            elif used_percent > 75:
                disk_state = "warn"
                disk_headline = "Disk space low"
            else:
                disk_state = "ok"
                disk_headline = "Disk space healthy"

            disk_status = DiagnosticStatus(
                name="disk",
                state=disk_state,
                headline=disk_headline,
                detail=f"{free_gb:.1f} GB free of {total_gb:.1f} GB ({used_percent:.1f}% used)",
                validated_at=utc_now(),
            )
        except Exception as exc:
            disk_status = DiagnosticStatus(
                name="disk",
                state="warn",
                headline="Disk check failed",
                detail=str(exc),
            )

        return {
            "credentials": cred_status,
            "sheets": sheets_status,
            "models": model_status,
            "database": database_status,
            "faiss": faiss_status,
            "disk": disk_status,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _resolve_secret_path(self, candidate: str) -> Path:
        candidate_path = Path(candidate).expanduser()
        if not candidate_path.is_absolute():
            candidate_path = (self._managed_credentials_dir() / candidate_path).resolve()
        else:
            candidate_path = candidate_path.resolve()
        return candidate_path

    def _managed_credentials_dir(self) -> Path:
        path = self._secrets_root / "credentials"
        path.mkdir(parents=True, exist_ok=True)
        return path

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
        category_sheet = data.get("category_sheet") or {}
        experiences_sheet = data.get("experiences_sheet") or {}
        manuals_sheet = data.get("manuals_sheet") or {}
        return SheetSettings(
            config_path=data.get("config_path", ""),
            config_checksum=record.checksum,
             data_path=data.get("data_path"),
             google_credentials_path=data.get("google_credentials_path"),
            category_sheet_id=category_sheet.get("id", ""),
            category_worksheet=category_sheet.get("worksheet", ""),
            experiences_sheet_id=experiences_sheet.get("id", ""),
            experiences_worksheet=experiences_sheet.get("worksheet", ""),
            manuals_sheet_id=manuals_sheet.get("id", ""),
            manuals_worksheet=manuals_sheet.get("worksheet", ""),
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
                detail="Load scripts_config.yaml with google_credentials_path set to a readable JSON file.",
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
                state="error",
                headline="Insecure credential permissions",
                detail=f"File permissions {oct(perms)} allow other users to read credentials. Run: chmod 600 {path}",
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
                detail="Load scripts_config.yaml so the server knows your sheet IDs.",
            )

        config_path = Path(data.get("config_path", "")).expanduser()
        if not config_path.exists():
            return DiagnosticStatus(
                name="sheets",
                state="error",
                headline="Config file not found",
                detail=str(config_path),
                validated_at=data.get("validated_at"),
            )

        def _extract(prefix: str) -> tuple[str, str]:
            sheet_id = (data.get(f"{prefix}_sheet_id") or "").strip()
            worksheet = (data.get(f"{prefix}_worksheet") or "").strip()
            if not sheet_id:
                raise SettingValidationError(f"Missing sheet ID for {prefix} sheet")
            if not worksheet:
                raise SettingValidationError(f"Missing worksheet name for {prefix} sheet")
            return sheet_id, worksheet

        try:
            cat_sheet_id, cat_ws = _extract("category")
            exp_sheet_id, exp_ws = _extract("experiences")
            man_sheet_id, man_ws = _extract("manuals")
        except SettingValidationError as exc:
            return DiagnosticStatus(
                name="sheets",
                state="error",
                headline=str(exc),
                detail=f"Source: {config_path}",
                validated_at=data.get("validated_at"),
            )

        summary_lines = []
        if cat_ws:
            # summary_lines.append(f"Categories → {cat_ws}")
            summary_lines.append(f"{cat_ws}, ")
        if exp_ws:
            # summary_lines.append(f"Experiences → {exp_ws}")
            summary_lines.append(f"{exp_ws}, ")
        if man_ws:
            # summary_lines.append(f"Manuals → {man_ws}")
            summary_lines.append(f"{man_ws} ")
        summary = "\n".join(summary_lines) 

        state = "ok"
        headline = "Sheet config loaded"

        if cred_status.state != "ok":
            state = "warn"
            headline = "Credentials not ready"
            summary = "Sheets will fail until credentials validate."

        return DiagnosticStatus(
            name="sheets",
            state=state,
            headline=headline,
            detail=summary,
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
