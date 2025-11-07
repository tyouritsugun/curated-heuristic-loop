"""Operations orchestration for import/export/index jobs."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from src.storage.schema import AuditLog, JobHistory, OperationLock, utc_now

logger = logging.getLogger(__name__)


class OperationConflict(Exception):
    """Raised when an operation lock cannot be acquired."""


class JobNotFoundError(Exception):
    """Raised when looking up a job that does not exist."""


OperationHandler = Callable[[Dict[str, Any], Session], Dict[str, Any]]


class OperationsService:
    """Schedules long-running jobs with advisory locks and audit logging."""

    def __init__(self, session_factory, max_workers: int = 3, lock_ttl_seconds: int = 3600):
        self._session_factory = session_factory
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._handlers: Dict[str, OperationHandler] = {
            "import": self._noop_handler,
            "export": self._noop_handler,
            "index": self._noop_handler,
        }
        self._lock = threading.Lock()
        self._lock_ttl = lock_ttl_seconds
        self._active_jobs: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Registration / shutdown
    # ------------------------------------------------------------------
    def register_handler(self, name: str, handler: OperationHandler) -> None:
        self._handlers[name] = handler

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

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
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

    def _noop_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        delay = float(payload.get("_test_delay", 0) or 0)
        if delay > 0:
            time.sleep(min(delay, 30.0))
        return {"message": "no-op", "received": payload}

    def _acquire_lock(self, session: Session, name: str, owner: str):
        now = datetime.now(timezone.utc)
        lock = session.query(OperationLock).filter(OperationLock.name == name).one_or_none()
        if lock:
            expires_at = None
            if lock.expires_at:
                try:
                    expires_at = datetime.fromisoformat(lock.expires_at)
                except ValueError:
                    expires_at = None
            if lock.owner != owner and (expires_at is None or expires_at > now):
                raise OperationConflict(f"Operation '{name}' already running")
            lock.owner = owner
            lock.created_at = utc_now()
        else:
            lock = OperationLock(name=name, owner=owner, created_at=utc_now())
            session.add(lock)
        if self._lock_ttl:
            expires = now + timedelta(seconds=self._lock_ttl)
            lock.expires_at = expires.isoformat()
        else:
            lock.expires_at = None
        session.flush()

    def _release_lock(self, name: str, owner: str, session: Optional[Session] = None):
        owns_session = False
        if session is None:
            session = self._session_factory()
            owns_session = True
        try:
            stmt = session.query(OperationLock).filter(
                OperationLock.name == name,
                OperationLock.owner == owner,
            )
            lock = stmt.one_or_none()
            if lock:
                session.delete(lock)
                session.commit()
        finally:
            if owns_session:
                session.close()

    def _job_row(self, session: Session, job_id: str) -> JobHistory:
        job = session.query(JobHistory).filter(JobHistory.job_id == job_id).one_or_none()
        if not job:
            raise JobNotFoundError(job_id)
        return job

    def _serialize_job(self, job: JobHistory) -> Dict[str, Any]:
        data = {
            "job_id": job.job_id,
            "job_type": job.job_type,
            "status": job.status,
            "requested_by": job.requested_by,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "cancelled_at": job.cancelled_at,
        }
        if job.payload:
            try:
                data["payload"] = json.loads(job.payload)
            except json.JSONDecodeError:
                data["payload"] = job.payload
        if job.result:
            try:
                data["result"] = json.loads(job.result)
            except json.JSONDecodeError:
                data["result"] = job.result
        if job.error_detail:
            data["error"] = job.error_detail
        return data
