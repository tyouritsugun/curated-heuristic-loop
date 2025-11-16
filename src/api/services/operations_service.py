"""Operations orchestration for import/export/index jobs."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional
import re

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

        # Legacy aliases (kept for compatibility with old job names in DB)
        self._handlers["import"] = self._import_sheets_handler
        self._handlers["sync"] = self._sync_embeddings_handler
        self._handlers["index"] = self._rebuild_index_handler

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
        return [self._serialize_job(row) for row in rows]

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
            "error_detail": job.error_detail,
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

        handler = self._handlers.get(job_type, self._noop_handler)
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
        """Import data from Google Sheets into the database."""
        from src.api.services.import_service import ImportService
        from src.common.sheets_client import SheetsClient
        
        try:
            # Get sheets data from payload (sent by HTTP client)
            categories_rows = payload.get("categories", [])
            experiences_rows = payload.get("experiences", [])
            manuals_rows = payload.get("manuals", [])
            
            if not categories_rows:
                raise ValueError("No category data provided in payload")
            
            # Import via service
            import_service = ImportService(self._data_path)
            counts = import_service.import_from_sheets(
                session=session,
                categories_rows=categories_rows,
                experiences_rows=experiences_rows,
                manuals_rows=manuals_rows,
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
                "counts": counts,
                "message": f"Imported {counts['experiences']} experiences, {counts['manuals']} manuals, {counts['categories']} categories"
            }
            
        except Exception as exc:
            logger.exception("Import failed")
            raise ValueError(f"Import operation failed: {exc}") from exc

    def _sync_embeddings_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        """Sync embeddings for pending/failed entities."""
        if not self._mode_adapter:
            raise ValueError("Embedding sync requires GPU mode (mode adapter not set)")
        
        if not self._mode_adapter.can_run_vector_jobs():
            raise ValueError("Vector operations not available in current mode")
        
        try:
            # Get embedding service from mode adapter
            embedding_service = self._mode_adapter.get_embedding_service()
            if not embedding_service:
                raise ValueError("Embedding service not available")
            
            # Get parameters
            retry_failed = payload.get("retry_failed", False)
            max_count = payload.get("max_count")
            
            # Process pending embeddings
            stats = embedding_service.process_pending(max_count=max_count)
            
            logger.info("Embedding sync completed: %s", stats)
            
            return {
                "success": True,
                "stats": stats,
                "message": f"Processed {stats['processed']} entities: {stats['succeeded']} succeeded, {stats['failed']} failed"
            }
            
        except Exception as exc:
            logger.exception("Embedding sync failed")
            raise ValueError(f"Embedding sync operation failed: {exc}") from exc

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




__all__ = [
    "OperationsService",
    "OperationConflict",
    "JobNotFoundError",
    "OperationHandler",
]
