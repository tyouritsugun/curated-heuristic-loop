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

from src.storage.schema import AuditLog, JobHistory, OperationLock, utc_now

logger = logging.getLogger(__name__)


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
        mode: Optional[str] = None,
        project_root: Optional[Path] = None,
    ):
        self._session_factory = session_factory
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._lock = threading.Lock()
        self._lock_ttl = lock_ttl_seconds
        self._active_jobs: Dict[str, str] = {}
        self._mode = (mode or os.getenv("CHL_OPERATIONS_MODE", "scripts")).strip().lower()
        self._project_root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[2]
        self._scripts_dir = self._project_root / "scripts"
        self._handlers: Dict[str, OperationHandler] = {}
        # Hard cap duration for external scripts (seconds)
        try:
            self._timeout_seconds = int(os.getenv("CHL_OPERATIONS_TIMEOUT_SEC", "900"))
        except ValueError:
            self._timeout_seconds = 900
        self._register_builtin_handlers()

    # ------------------------------------------------------------------
    # Registration / shutdown
    # ------------------------------------------------------------------
    def register_handler(self, name: str, handler: OperationHandler) -> None:
        self._handlers[name] = handler

    def _register_builtin_handlers(self):
        """Register default handlers based on the configured mode."""
        if self._mode not in {"scripts", "noop"}:
            logger.warning("Unknown CHL_OPERATIONS_MODE '%s'; defaulting to 'noop'.", self._mode)
            self._mode = "noop"

        if self._mode == "scripts":
            if self._scripts_dir.exists():
                self._handlers["import"] = self._import_handler
                self._handlers["export"] = self._export_handler
                self._handlers["index"] = self._index_handler
                return
            logger.warning(
                "OperationsService mode 'scripts' requested but scripts directory '%s' is missing. "
                "Falling back to no-op handlers.",
                self._scripts_dir,
            )
            self._mode = "noop"

        # Default noop handlers
        self._handlers.setdefault("import", self._noop_handler)
        self._handlers.setdefault("export", self._noop_handler)
        self._handlers.setdefault("index", self._noop_handler)

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
            .all()
        )
        return {row.job_type: self._serialize_job(row) for row in rows}

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
        self._simulate_delay(payload)
        return {"message": "no-op", "received": payload or {}, "mode": self._mode}

    def _import_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        del session  # Handler is side-effect only; DB session not required
        command = [
            sys.executable,
            str(self._scripts_dir / "import.py"),
            "--yes",
            "--skip-api-coordination",
        ]
        payload = payload or {}
        if config := payload.get("config"):
            command.extend(["--config", str(config)])
        return self._run_script(command, payload)

    def _export_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        del session
        command = [
            sys.executable,
            str(self._scripts_dir / "export.py"),
        ]
        payload = payload or {}
        if payload.get("dry_run"):
            command.append("--dry-run")
        if config := payload.get("config"):
            command.extend(["--config", str(config)])
        return self._run_script(command, payload)

    def _index_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        del session
        command = [
            sys.executable,
            str(self._scripts_dir / "rebuild_index.py"),
        ]
        payload = payload or {}
        return self._run_script(command, payload)

    def _simulate_delay(self, payload: Optional[Dict[str, Any]]) -> None:
        if not payload:
            return
        try:
            delay = float(payload.get("_test_delay", 0) or 0)
        except (TypeError, ValueError):
            return
        if delay > 0:
            time.sleep(min(delay, 30.0))

    def _run_script(self, command: list[str], payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        self._simulate_delay(payload)
        if self._mode != "scripts":
            return {"message": "skipped (mode != scripts)", "command": command, "mode": self._mode}
        if not self._scripts_dir.exists():
            raise RuntimeError(f"Scripts directory not found: {self._scripts_dir}")

        env = os.environ.copy()
        # Only allow safe overrides of CHL_* variables to avoid injecting arbitrary env
        if payload and isinstance(payload.get("env"), dict):
            allowed_key = re.compile(r"^[A-Z0-9_]{3,64}$")
            for key, value in payload["env"].items():
                k = str(key)
                if not allowed_key.match(k):
                    logger.warning("Blocked env override for disallowed key: %s", k)
                    continue
                if not k.startswith("CHL_"):
                    logger.warning("Blocked env override for non-CHL key: %s", k)
                    continue
                env[k] = str(value)

        start = time.perf_counter()
        logger.info("Running operation command: %s", " ".join(command))
        try:
            proc = subprocess.run(
                command,
                cwd=str(self._project_root),
                capture_output=True,
                text=True,
                env=env,
                timeout=max(60, self._timeout_seconds),
            )
        except subprocess.TimeoutExpired as exc:
            duration = round(time.perf_counter() - start, 3)
            tail = self._tail_text((exc.stdout or "") + "\n" + (exc.stderr or ""))
            raise RuntimeError(
                f"Command {' '.join(command)} timed out after {duration:.1f}s: {tail}"
            ) from exc
        duration = round(time.perf_counter() - start, 3)

        result = {
            "command": command,
            "cwd": str(self._project_root),
            "exit_code": proc.returncode,
            "stdout_tail": self._tail_text(proc.stdout),
            "stderr_tail": self._tail_text(proc.stderr),
            "duration_seconds": duration,
            "mode": self._mode,
        }

        if proc.returncode != 0:
            raise RuntimeError(
                f"Command {' '.join(command)} failed with exit code {proc.returncode}: {result['stderr_tail'] or result['stdout_tail']}"
            )

        return result

    @staticmethod
    def _tail_text(text: Optional[str], limit: int = 2000) -> str:
        if not text:
            return ""
        text = text.strip()
        if len(text) <= limit:
            return text
        return text[-limit:]

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
