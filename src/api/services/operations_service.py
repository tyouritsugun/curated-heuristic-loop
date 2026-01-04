"""Operations orchestration for import/export/index jobs."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.common.storage.schema import AuditLog, JobHistory, OperationLock, utc_now

logger = logging.getLogger(__name__)

QUEUE_COORD_SOURCE_SYNC = "operations_service.sync"


def _log_queue_coordination(stage: str, **fields):
    payload = {
        "event": "queue_coordination",
        "source": QUEUE_COORD_SOURCE_SYNC,
        "stage": stage,
    }
    for key, value in fields.items():
        if value is not None:
            payload[key] = value
    logger.info("queue_coordination %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))


class OperationConflict(Exception):
    """Raised when an operation lock cannot be acquired."""


class JobNotFoundError(Exception):
    """Raised when looking up a job that does not exist."""


OperationHandler = Callable[[Dict[str, Any], Session], Dict[str, Any]]


class OperationsService:
    """Schedules long-running jobs with advisory locks and audit logging."""

    def __init__(
        self,
        session_factory,
        max_workers: int = 3,
        lock_ttl_seconds: int = 3600,
        data_path: Optional[Path] = None,
        faiss_index_path: Optional[Path] = None,
    ):
        """Initialize operations service.

        Args:
            session_factory: Factory function that creates database sessions
            max_workers: Maximum concurrent operations
            lock_ttl_seconds: Time-to-live for operation locks
            data_path: Path to data directory (for imports/FAISS index)
        """
        self._session_factory = session_factory
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.Lock()
        self._lock_ttl = lock_ttl_seconds
        self._active_jobs: Dict[str, str] = {}
        self._handlers: Dict[str, OperationHandler] = {}

        # Get data path for import service
        if data_path is None:
            project_root = Path(__file__).resolve().parents[3]
            data_path = project_root / "data"
        self._data_path = Path(data_path)
        self._faiss_index_path = Path(faiss_index_path) if faiss_index_path else None

        # Mode adapter for GPU operations (set later by runtime)
        self._mode_adapter: Optional[Any] = None

        # Register handlers
        self._register_builtin_handlers()

    # ------------------------------------------------------------------
    # Registration / shutdown
    # ------------------------------------------------------------------
    def register_handler(self, name: str, handler: OperationHandler) -> None:
        self._handlers[name] = handler

    def set_mode_adapter(self, adapter: Any) -> None:
        """Attach a mode-specific adapter for vector-capable operations."""
        self._mode_adapter = adapter

    def _register_builtin_handlers(self):
        """Register operation handlers that call services directly."""
        # Core operations
        self._handlers["import-sheets"] = self._import_sheets_handler
        self._handlers["sync-embeddings"] = self._sync_embeddings_handler
        self._handlers["rebuild-index"] = self._rebuild_index_handler
        self._handlers["export"] = self._export_snapshot_handler
        self._handlers["export-snapshot"] = self._export_snapshot_handler

        # Legacy aliases (kept for compatibility with old job names in DB)
        self._handlers["import"] = self._import_sheets_handler
        self._handlers["sync"] = self._sync_embeddings_handler
        self._handlers["index"] = self._rebuild_index_handler
        self._handlers["export-job"] = self._export_snapshot_handler
        self._handlers["import-excel"] = self._import_excel_handler
        self._handlers["export-excel"] = self._export_excel_handler

    def shutdown(self):
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def trigger(self, job_type: str, payload: Optional[Dict[str, Any]], actor: Optional[str]) -> Dict[str, Any]:
        """Trigger an operation if no conflicting lock exists."""
        if job_type not in self._handlers:
            raise ValueError(f"Unsupported operation type: {job_type}")

        job_id = str(uuid.uuid4())
        session = self._session_factory()
        try:
            self._acquire_lock(session, job_type, job_id)
            job = JobHistory(
                job_id=job_id,
                job_type=job_type,
                status="queued",
                requested_by=actor,
                payload=json.dumps(payload or {}, ensure_ascii=False),
                created_at=utc_now(),
            )
            session.add(job)
            session.add(
                AuditLog(
                    event_type=f"operations.{job_type}.queued",
                    actor=actor,
                    context=json.dumps(payload or {}, ensure_ascii=False),
                    created_at=utc_now(),
                )
            )
            session.commit()
        finally:
            session.close()

        # Submit async job
        future = self._executor.submit(self._run_job, job_id, job_type, payload or {})
        with self._lock:
            self._active_jobs[job_id] = job_type
        future.add_done_callback(lambda _: self._active_jobs.pop(job_id, None))

        return {"job_id": job_id, "status": "queued"}

    def get_job(self, job_id: str, session: Session) -> Dict[str, Any]:
        job = self._job_row(session, job_id)
        return self._serialize_job(job)

    def cancel_job(self, job_id: str, actor: Optional[str]) -> Dict[str, Any]:
        session = self._session_factory()
        try:
            job = self._job_row(session, job_id)
            if job.status in {"succeeded", "failed", "cancelled"}:
                return self._serialize_job(job)
            job.status = "cancelled"
            job.cancelled_at = utc_now()
            session.add(
                AuditLog(
                    event_type=f"operations.{job.job_type}.cancelled",
                    actor=actor,
                    context=json.dumps({"job_id": job_id}, ensure_ascii=False),
                    created_at=utc_now(),
                )
            )
            session.commit()
        finally:
            session.close()

        self._release_lock(job.job_type, job_id)
        response_session = self._session_factory()
        try:
            return self.get_job(job_id, response_session)
        finally:
            response_session.close()

    def list_recent(self, session: Session, limit: int = 10):
        rows = (
            session.query(JobHistory)
            .order_by(JobHistory.created_at.desc())
            .limit(limit)
            .all()
        )
        return [self._serialize_job(row) for row in rows]

    def last_runs_by_type(self, session: Session):
        """Return the most recent job per operation type."""
        subquery = (
            session.query(
                JobHistory.job_type.label("job_type"),
                func.max(JobHistory.created_at).label("recent_created_at"),
            )
            .group_by(JobHistory.job_type)
            .subquery()
        )
        rows = (
            session.query(JobHistory)
            .join(
                subquery,
                (JobHistory.job_type == subquery.c.job_type)
                & (JobHistory.created_at == subquery.c.recent_created_at),
            )
            .order_by(JobHistory.job_type.asc())
            .all()
        )
        serialized = [self._serialize_job(row) for row in rows]
        return {record["job_type"]: record for record in serialized}

    # ------------------------------------------------------------------
    # Internal job helpers
    # ------------------------------------------------------------------
    def _job_row(self, session: Session, job_id: str) -> JobHistory:
        job = session.query(JobHistory).filter(JobHistory.job_id == job_id).one_or_none()
        if job is None:
            raise JobNotFoundError(f"Job not found: {job_id}")
        return job

    def _serialize_job(self, job: JobHistory) -> Dict[str, Any]:
        payload = {}
        if job.payload:
            try:
                payload = json.loads(job.payload)
            except json.JSONDecodeError:
                payload = {"raw": job.payload}
        result = {}
        if job.result:
            try:
                result = json.loads(job.result)
            except json.JSONDecodeError:
                result = {"raw": job.result}
        return {
            "job_id": job.job_id,
            "job_type": job.job_type,
            "status": job.status,
            "requested_by": job.requested_by,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "cancelled_at": job.cancelled_at,
            "payload": payload,
            "result": result,
            "error": job.error_detail,
        }

    # ------------------------------------------------------------------
    # Lock helpers
    # ------------------------------------------------------------------
    def _acquire_lock(self, session: Session, job_type: str, job_id: str) -> None:
        now = utc_now()
        existing = (
            session.query(OperationLock)
            .filter(OperationLock.name == job_type)
            .one_or_none()
        )
        if existing and existing.expires_at:
            try:
                expires_at = datetime.fromisoformat(existing.expires_at)
            except Exception:
                expires_at = None
            if expires_at and expires_at > now:
                raise OperationConflict(
                    f"Operation '{job_type}' already running (job_id={existing.owner_id})"
                )

        expires_at = (now + timedelta(seconds=self._lock_ttl)).isoformat()
        if existing is None:
            lock = OperationLock(
                name=job_type,
                owner_id=job_id,
                created_at=now,
                expires_at=expires_at,
            )
            session.add(lock)
        else:
            existing.owner_id = job_id
            existing.created_at = now
            existing.expires_at = expires_at
        session.commit()

    def _release_lock(self, job_type: str, job_id: str, session: Optional[Session] = None) -> None:
        owns_session = False
        if session is None:
            session = self._session_factory()
            owns_session = True
        try:
            lock = (
                session.query(OperationLock)
                .filter(OperationLock.name == job_type, OperationLock.owner_id == job_id)
                .one_or_none()
            )
            if lock:
                session.delete(lock)
                session.commit()
        finally:
            if owns_session:
                session.close()

    def _run_job(self, job_id: str, job_type: str, payload: Dict[str, Any]):
        logger.info("Starting job %s (%s)", job_id, job_type)
        session = self._session_factory()
        job = self._job_row(session, job_id)
        if job.status == "cancelled":
            logger.info("Job %s was cancelled before start", job_id)
            self._release_lock(job_type, job_id, session)
            session.close()
            return
        job.status = "running"
        job.started_at = utc_now()
        session.commit()
        session.close()

        # Handler is guaranteed to exist due to validation in trigger()
        handler = self._handlers[job_type]
        handler_session = self._session_factory()
        error_detail = None
        result_payload: Optional[Dict[str, Any]] = None
        try:
            result_payload = handler(payload, handler_session)
            handler_session.commit()
            status = "succeeded"
        except Exception as exc:  # pragma: no cover - defensive
            handler_session.rollback()
            error_detail = str(exc)
            status = "failed"
            logger.exception("Job %s failed: %s", job_id, exc)
        finally:
            handler_session.close()

        finish_session = self._session_factory()
        try:
            job = self._job_row(finish_session, job_id)
            if job.status == "cancelled":
                status = "cancelled"
            job.status = status
            job.finished_at = utc_now()
            if result_payload is not None:
                job.result = json.dumps(result_payload, ensure_ascii=False)
            if error_detail:
                job.error_detail = error_detail
            finish_session.commit()
        finally:
            finish_session.close()

        self._release_lock(job_type, job_id)
        logger.info("Job %s completed with status=%s", job_id, status)

    # ------------------------------------------------------------------
    # Operation Handlers (direct service calls)
    # ------------------------------------------------------------------
    def _import_sheets_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        """Import data from Google Sheets into the database.

        Supports two modes:
        1. With payload: Import data from provided rows (API clients/automation)
        2. Without payload: Fetch data from Google Sheets and import (used by the web UI)
        """
        from src.api.services.import_service import ImportService
        from src.common.config.config import get_config
        import os
        from pathlib import Path

        try:
            config = get_config()
            skills_enabled = bool(getattr(config, "skills_enabled", True))

            # Get sheets data from payload (sent by HTTP client)
            categories_rows: list[dict] = []
            experiences_rows = payload.get("experiences") or []
            skills_rows = payload.get("skills") or payload.get("manuals") or []
            if not skills_enabled:
                skills_rows = []

            # If no payload provided, fetch from Google Sheets directly
            if not categories_rows:
                logger.info("No payload provided, fetching data from Google Sheets")

                # Get credentials and spreadsheet ID from environment
                credentials_env = os.getenv("GOOGLE_CREDENTIAL_PATH")
                spreadsheet_id = os.getenv("IMPORT_SPREADSHEET_ID")

                if not credentials_env:
                    raise ValueError("GOOGLE_CREDENTIAL_PATH not set in .env file")
                if not spreadsheet_id:
                    raise ValueError("IMPORT_SPREADSHEET_ID not set in .env file")

                # Resolve credentials path relative to project root
                from src.common.config.config import PROJECT_ROOT
                credentials_path = Path(credentials_env)
                if not credentials_path.is_absolute():
                    credentials_path = (PROJECT_ROOT / credentials_path).resolve()

                if not credentials_path.exists():
                    raise ValueError(f"Credential file not found: {credentials_path}")

                # Fetch data from Google Sheets
                from src.common.storage.sheets_client import SheetsClient
                sheets_client = SheetsClient(str(credentials_path))

                # Fetch from configured worksheets
                experiences_rows = sheets_client.read_worksheet(spreadsheet_id, "Experiences")

                if skills_enabled:
                    # Try "Skills" first (new), fall back to "Manuals" (legacy) for backward compatibility
                    skills_rows = sheets_client.read_worksheet(spreadsheet_id, "Skills")
                    if not skills_rows:
                        skills_rows = sheets_client.read_worksheet(spreadsheet_id, "Manuals")
                else:
                    skills_rows = []

                logger.info(
                    "Fetched from Google Sheets: %d experiences, %d skills",
                    len(experiences_rows),
                    len(skills_rows),
                )

                if not experiences_rows and not skills_rows:
                    return {
                        "success": True,
                        "counts": {"experiences": 0, "skills": 0},
                        "message": "No data found in Google Sheets",
                    }

            # Import via service
            import_service = ImportService(self._data_path, self._faiss_index_path)
            counts = import_service.import_from_sheets(
                session=session,
                categories_rows=categories_rows,
                experiences_rows=experiences_rows,
                skills_rows=skills_rows,
            )

            logger.info("Import completed: %s", counts)

            # Auto-trigger sync job if import succeeded and GPU mode is enabled
            if self._mode_adapter and self._mode_adapter.can_run_vector_jobs():
                try:
                    logger.info("Import succeeded, triggering automatic embedding sync...")
                    self.trigger(job_type="sync-embeddings", payload={}, actor="system:auto_import")
                except OperationConflict:
                    logger.warning("Sync job already running, skipping automatic trigger")
                except Exception as e:
                    logger.error(f"Failed to trigger automatic sync job: {e}")

            return {
                "success": True,
                "counts": {"experiences": counts["experiences"], "skills": counts["skills"]},
                "message": f"Imported {counts['experiences']} experiences, {counts['skills']} skills",
            }

        except Exception as exc:
            logger.exception("Import failed")
            # Extract just the error type and first line of message for UI display
            error_type = type(exc).__name__
            error_msg = str(exc).split('\n')[0][:200]  # First line, max 200 chars
            raise ValueError(f"Import failed ({error_type}): {error_msg}") from exc

    def _sync_embeddings_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        """Sync embeddings for pending/failed entities."""
        if not self._mode_adapter:
            raise ValueError("Embedding sync requires GPU mode (mode adapter not set)")

        if not self._mode_adapter.can_run_vector_jobs():
            raise ValueError("Vector operations not available in current mode")

        try:
            # Get embedding service and underlying session from mode adapter
            embedding_service_tuple = self._mode_adapter.get_embedding_service()
            if not embedding_service_tuple:
                raise ValueError("Embedding service not available")
            embedding_service, service_session = embedding_service_tuple

            retry_failed = bool(payload.get("retry_failed"))
            max_count = payload.get("max_count")
            stats = embedding_service.process_pending(max_count=max_count)
            retry_result = None
            if retry_failed:
                retry_result = embedding_service.retry_failed(max_count=max_count)

            logger.info("Embedding sync completed: %s", stats)

            return {
                "success": True,
                "stats": stats,
                "retry": retry_result,
                "message": _format_sync_message(stats, retry_result),
            }

        except Exception as exc:
            logger.exception("Embedding sync failed")
            raise ValueError(f"Embedding sync operation failed: {exc}") from exc
        finally:
            try:
                if "service_session" in locals() and service_session is not None:
                    service_session.close()
            except Exception:
                pass

    def _rebuild_index_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        """Rebuild FAISS index from existing embeddings."""
        if not self._mode_adapter:
            raise ValueError("Index rebuild requires GPU mode (mode adapter not set)")
        
        if not self._mode_adapter.can_run_vector_jobs():
            raise ValueError("Vector operations not available in current mode")
        
        try:
            # Get search provider from mode adapter
            search_provider = self._mode_adapter.get_search_provider()
            if not search_provider:
                raise ValueError("Search provider not available")
            
            # Rebuild index
            search_provider.rebuild_index(session)
            
            logger.info("FAISS index rebuild completed successfully")
            
            return {
                "success": True,
                "message": "FAISS index rebuilt successfully from existing embeddings"
            }
            
        except Exception as exc:
            logger.exception("Index rebuild failed")
            raise ValueError(f"Index rebuild operation failed: {exc}") from exc

    def _export_snapshot_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        """Export database entries to Google Sheets."""
        import os
        from pathlib import Path
        from src.common.storage.schema import Experience, CategorySkill
        from src.common.storage.sheets_client import SheetsClient
        from src.common.config.config import PROJECT_ROOT, get_config
        from src.common.config.categories import get_categories

        try:
            delay = float(payload.get("_test_delay", 0) or 0)
        except (TypeError, ValueError):
            delay = 0
        if delay > 0:
            time.sleep(min(delay, 30))

        # Get credentials and spreadsheet ID from environment
        credentials_env = os.getenv("GOOGLE_CREDENTIAL_PATH")
        spreadsheet_id = os.getenv("EXPORT_SPREADSHEET_ID")

        if not credentials_env:
            raise ValueError("GOOGLE_CREDENTIAL_PATH not set in .env file")
        if not spreadsheet_id:
            raise ValueError("EXPORT_SPREADSHEET_ID not set in .env file")

        # Resolve credentials path relative to project root
        credentials_path = Path(credentials_env)
        if not credentials_path.is_absolute():
            credentials_path = (PROJECT_ROOT / credentials_path).resolve()

        if not credentials_path.exists():
            raise ValueError(f"Credential file not found: {credentials_path}")

        config = get_config()
        skills_enabled = bool(getattr(config, "skills_enabled", True))

        # Query data from database (categories are code-defined; not exported)
        experiences = session.query(Experience).order_by(Experience.updated_at.desc()).all()
        skills = []
        if skills_enabled:
            skills = session.query(CategorySkill).order_by(CategorySkill.updated_at.desc()).all()

        # Initialize sheets client
        sheets_client = SheetsClient(str(credentials_path))

        # Get worksheet names from environment (with defaults)
        experiences_worksheet = os.getenv("EXPORT_WORKSHEET_EXPERIENCES", "Experiences")
        skills_worksheet = os.getenv("EXPORT_WORKSHEET_SKILLS", "Skills")

        # Export Experiences
        experiences_headers = ["id", "category_code", "section", "title", "playbook", "context", "updated_at", "author", "source"]
        experiences_rows = [
            [
                exp.id,
                exp.category_code,
                exp.section,
                exp.title,
                exp.playbook or "",
                exp.context or "",
                exp.updated_at.isoformat() if exp.updated_at else "",
                exp.author or "",
                exp.source or "",
            ]
            for exp in experiences
        ]
        sheets_client.write_worksheet(
            spreadsheet_id,
            experiences_worksheet,
            experiences_headers,
            experiences_rows,
            readonly_cols=[0, 6],  # id and updated_at are readonly
        )

        # Export Skills (if enabled)
        if skills_enabled:
            skills_headers = [
                "id",
                "category_code",
                "name",
                "description",
                "content",
                "license",
                "compatibility",
                "metadata",
                "allowed_tools",
                "model",
                "updated_at",
                "author",
            ]
            skills_rows = [
                [
                    skill.id,
                    skill.category_code,
                    skill.name,
                    skill.description,
                    skill.content or "",
                    skill.license or "",
                    skill.compatibility or "",
                    skill.metadata_json or "",
                    skill.allowed_tools or "",
                    skill.model or "",
                    skill.updated_at.isoformat() if skill.updated_at else "",
                    skill.author or "",
                ]
                for skill in skills
            ]
            sheets_client.write_worksheet(
                spreadsheet_id,
                skills_worksheet,
                skills_headers,
                skills_rows,
                readonly_cols=[0, 10],  # id and updated_at are readonly
            )

        logger.info(
            "Export completed: %d experiences, %d skills to spreadsheet %s",
            len(experiences),
            len(skills),
            spreadsheet_id[:8],
        )

        return {
            "success": True,
            "counts": {
                "experiences": len(experiences),
                "skills": len(skills),
            },
            "message": f"Exported to Google Sheets: {len(experiences)} experiences, {len(skills)} skills",
        }

    def _import_excel_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        """Import data from Excel file into the database.

        Payload should contain:
        - file_path: path to the Excel file to import
        """
        from src.api.services.import_service import ImportService
        from src.common.config.config import get_config
        import os
        from pathlib import Path
        import pandas as pd
        import zipfile

        try:
            config = get_config()
            skills_enabled = bool(getattr(config, "skills_enabled", True))

            # Get file path from payload
            file_path = payload.get("file_path")
            if not file_path:
                raise ValueError("Excel file path not provided in payload")

            file_path = Path(file_path)
            if not file_path.exists():
                raise ValueError(f"Excel file not found: {file_path}")

            # Validate that this is a proper Excel file by checking if it's a zip file
            # Since .xlsx files are ZIP archives, and also check if file has content
            import os
            file_size = os.path.getsize(file_path)
            if file_size == 0:
                raise ValueError(f"Excel file is empty: {file_path}")

            try:
                with zipfile.ZipFile(file_path, 'r') as zip_file:
                    # This will raise an exception if it's not a valid zip file
                    zip_file.testzip()
            except zipfile.BadZipFile:
                raise ValueError(f"Invalid Excel file format: {file_path} is not a valid .xlsx file")
            except Exception:
                # If we can't read it as a zip, it might be an old .xls format
                logger.info(f"File {file_path} might be .xls format, proceeding with import attempt")

            # Read Excel file - assume same structure as Google Sheets
            experiences_df = pd.DataFrame()
            skills_df = pd.DataFrame()

            try:
                experiences_df = pd.read_excel(file_path, sheet_name='Experiences', engine='openpyxl')
            except Exception:
                experiences_df = pd.DataFrame()

            if skills_enabled:
                try:
                    skills_df = pd.read_excel(file_path, sheet_name='Skills', engine='openpyxl')
                except Exception:
                    try:
                        skills_df = pd.read_excel(file_path, sheet_name='Manuals', engine='openpyxl')
                    except Exception:
                        skills_df = pd.DataFrame()

            # Convert DataFrames to the format expected by import_service
            categories_rows = []
            experiences_rows = experiences_df.to_dict('records') if not experiences_df.empty else []
            skills_rows = skills_df.to_dict('records') if not skills_df.empty else []

            # Import via service
            import_service = ImportService(self._data_path, self._faiss_index_path)
            counts = import_service.import_from_sheets(
                session=session,
                categories_rows=categories_rows,
                experiences_rows=experiences_rows,
                skills_rows=skills_rows,
            )

            logger.info("Excel import completed: %s", counts)

            # Auto-trigger sync job if import succeeded and GPU mode is enabled
            if self._mode_adapter and self._mode_adapter.can_run_vector_jobs():
                try:
                    logger.info("Import succeeded, triggering automatic embedding sync...")
                    self.trigger(job_type="sync-embeddings", payload={}, actor="system:auto_import")
                except OperationConflict:
                    logger.warning("Sync job already running, skipping automatic trigger")
                except Exception as e:
                    logger.error(f"Failed to trigger automatic sync job: {e}")

            return {
                "success": True,
                "counts": {"experiences": counts["experiences"], "skills": counts["skills"]},
                "message": f"Imported from Excel: {counts['experiences']} experiences, {counts['skills']} skills",
            }

        except Exception as exc:
            logger.exception("Excel import failed")
            # Extract just the error type and first line of message for UI display
            error_type = type(exc).__name__
            error_msg = str(exc).split('\n')[0][:200]  # First line, max 200 chars
            raise ValueError(f"Excel import failed ({error_type}): {error_msg}") from exc

    def _export_excel_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        """Export database entries to Excel file."""
        import os
        from pathlib import Path
        import pandas as pd
        from src.common.storage.schema import Experience, CategorySkill
        from src.common.config.config import get_config
        from src.common.config.categories import get_categories

        try:
            config = get_config()
            skills_enabled = bool(getattr(config, "skills_enabled", True))
            # Get export path from payload or use default
            export_path = payload.get("export_path")
            if not export_path:
                # Create default export path in data directory
                from src.common.config.config import PROJECT_ROOT
                export_path = PROJECT_ROOT / "data" / f"export_{int(time.time())}.xlsx"

            export_path = Path(export_path)

            # Create directory if it doesn't exist
            export_path.parent.mkdir(parents=True, exist_ok=True)

            # Query all data from database (categories from canonical taxonomy)
            experiences = session.query(Experience).order_by(Experience.updated_at.desc()).all()
            skills = []
            if skills_enabled:
                skills = session.query(CategorySkill).order_by(CategorySkill.updated_at.desc()).all()

            # Convert to DataFrames
            experiences_data = []
            for exp in experiences:
                experiences_data.append({
                    "id": exp.id,
                    "category_code": exp.category_code,
                    "section": exp.section,
                    "title": exp.title,
                    "playbook": exp.playbook or "",
                    "context": exp.context or "",
                    "updated_at": exp.updated_at.isoformat() if exp.updated_at else "",
                    "author": exp.author or "",
                    "source": exp.source or "",
                })
            experiences_df = pd.DataFrame(experiences_data)

            skills_df = pd.DataFrame()
            if skills_enabled:
                skills_data = []
                for skill in skills:
                    skills_data.append({
                        "id": skill.id,
                        "category_code": skill.category_code,
                        "name": skill.name,
                        "description": skill.description,
                        "content": skill.content or "",
                        "license": skill.license or "",
                        "compatibility": skill.compatibility or "",
                        "metadata": skill.metadata_json or "",
                        "allowed_tools": skill.allowed_tools or "",
                        "model": skill.model or "",
                        "updated_at": skill.updated_at.isoformat() if skill.updated_at else "",
                        "author": skill.author or "",
                    })
                skills_df = pd.DataFrame(skills_data)

            # Write to Excel file with multiple sheets
            with pd.ExcelWriter(export_path, engine='openpyxl') as writer:
                experiences_df.to_excel(writer, sheet_name='Experiences', index=False)
                if skills_enabled:
                    skills_df.to_excel(writer, sheet_name='Skills', index=False)

            logger.info(
                "Excel export completed: %d experiences, %d skills to %s",
                len(experiences),
                len(skills),
                export_path,
            )

            return {
                "success": True,
                "export_path": str(export_path),
                "counts": {
                    "experiences": len(experiences),
                    "skills": len(skills),
                },
                "message": f"Exported to Excel: {len(experiences)} experiences, {len(skills)} skills",
            }

        except Exception as exc:
            logger.exception("Excel export failed")
            raise ValueError(f"Excel export operation failed: {exc}") from exc


__all__ = [
    "OperationsService",
    "OperationConflict",
    "JobNotFoundError",
    "OperationHandler",
]


def _normalize_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _summarize(content: str) -> Optional[str]:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return None


def _format_sync_message(stats: Dict[str, int], retry_result: Optional[Dict[str, int]]) -> str:
    base = (
        f"Processed {stats.get('processed', 0)} entities: "
        f"{stats.get('succeeded', 0)} succeeded, {stats.get('failed', 0)} failed."
    )
    if retry_result:
        base += (
            f" Retried {retry_result.get('retried', 0)} failures "
            f"({retry_result.get('succeeded', 0)} succeeded, {retry_result.get('failed', 0)} failed)."
        )
    return base
