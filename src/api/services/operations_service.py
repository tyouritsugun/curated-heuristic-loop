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
        self._timeout_seconds = self._load_timeout_config()
        self._operations_adapter: Optional[Any] = None
        self._register_builtin_handlers()

    # ------------------------------------------------------------------
    # Registration / shutdown
    # ------------------------------------------------------------------
    def register_handler(self, name: str, handler: OperationHandler) -> None:
        self._handlers[name] = handler

    def set_mode_adapter(self, adapter: Any) -> None:
        """Attach a mode-specific adapter for vector-capable operations."""
        self._operations_adapter = adapter

    def _register_builtin_handlers(self):
        """Register default handlers based on the configured mode."""
        if self._mode not in {"scripts", "noop"}:
            logger.warning("Unknown CHL_OPERATIONS_MODE '%s'; defaulting to 'noop'.", self._mode)
            self._mode = "noop"

        if self._mode == "scripts":
            if self._scripts_dir.exists():
                # Legacy job names
                self._handlers["import"] = self._import_handler
                self._handlers["export"] = self._export_handler
                self._handlers["index"] = self._index_handler
                self._handlers["sync"] = self._sync_handler
                self._handlers["reembed"] = self._reembed_handler
                self._handlers["guidelines"] = self._guidelines_handler

                # API-facing aliases (Phase 0 contract)
                self._handlers.setdefault("import-sheets", self._import_handler)
                self._handlers.setdefault("sync-embeddings", self._sync_handler)
                self._handlers.setdefault("rebuild-index", self._index_handler)
                self._handlers.setdefault("sync-guidelines", self._guidelines_handler)
                self._handlers.setdefault("seed-defaults", self._seed_defaults_handler)
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
        self._handlers.setdefault("sync", self._noop_handler)
        self._handlers.setdefault("reembed", self._noop_handler)
        self._handlers.setdefault("guidelines", self._noop_handler)
        self._handlers.setdefault("import-sheets", self._noop_handler)
        self._handlers.setdefault("sync-embeddings", self._noop_handler)
        self._handlers.setdefault("rebuild-index", self._noop_handler)
        self._handlers.setdefault("sync-guidelines", self._noop_handler)
        self._handlers.setdefault("seed-defaults", self._noop_handler)

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

    def _noop_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        self._simulate_delay(payload)
        return {"message": "no-op", "received": payload or {}, "mode": self._mode}

    def _import_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        del session  # Handler is side-effect only; DB session not required
        command = [
            sys.executable,
            str(self._scripts_dir / "import.py"),
            "--yes",
        ]
        # Allow reverting to legacy behavior via env flag
        if os.getenv("CHL_SKIP_API_COORDINATION", "0") == "1":
            command.append("--skip-api-coordination")
        payload = payload or {}
        if config := payload.get("config"):
            command.extend(["--config", str(config)])
        result = self._run_script(command, payload)

        # Auto-trigger sync job if import succeeded
        if result.get("exit_code") == 0:
            adapter = getattr(self, "_operations_adapter", None)
            vector_enabled = bool(adapter is None or adapter.can_run_vector_jobs())
            if vector_enabled:
                try:
                    logger.info("Import succeeded, triggering automatic sync job...")
                    self.trigger(job_type="sync", payload={}, actor="system:auto_import")
                except OperationConflict:
                    logger.warning("Sync job already running, skipping automatic trigger")
                except Exception as e:
                    logger.error(f"Failed to trigger automatic sync job: {e}")
            else:
                logger.info("Import succeeded; skipping automatic sync (vector jobs disabled by mode)")

        return result

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

    def _guidelines_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        """Refresh generator/evaluator manuals from markdown sources."""
        del session
        payload = payload or {}
        command = [
            sys.executable,
            str(self._scripts_dir / "seed_default_content.py"),
            "--skip-seed",
        ]
        if payload.get("skip_guidelines"):
            command.append("--skip-guidelines")
        if config := payload.get("config"):
            command.extend(["--config", str(config)])
        return self._run_script(command, payload)

    def _sync_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        """Synchronize embeddings in the background worker."""
        del session
        payload = payload or {}
        command = [
            sys.executable,
            str(self._scripts_dir / "sync_embeddings.py"),
        ]
        if payload.get("retry_failed"):
            command.append("--retry-failed")
        if max_count := payload.get("max_count"):
            command.extend(["--max-count", str(max_count)])
        if config := payload.get("config"):
            command.extend(["--config", str(config)])
        return self._run_script(command, payload)

    def _index_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        """Rebuild FAISS index from existing embeddings."""
        del session
        payload = payload or {}
        command = [
            sys.executable,
            str(self._scripts_dir / "rebuild_index.py"),
        ]
        if config := payload.get("config"):
            command.extend(["--config", str(config)])
        return self._run_script(command, payload)

    def _reembed_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        """Recompute embeddings for all entries (legacy)."""
        del session
        payload = payload or {}
        command = [
            sys.executable,
            str(self._scripts_dir / "rebuild_index.py"),
            "--reembed",
        ]
        if config := payload.get("config"):
            command.extend(["--config", str(config)])
        return self._run_script(command, payload)

    def _seed_defaults_handler(self, payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
        """Seed default content and/or guidelines based on payload flags."""
        del session
        payload = payload or {}
        command = [
            sys.executable,
            str(self._scripts_dir / "seed_default_content.py"),
        ]
        if payload.get("skip_seed"):
            command.append("--skip-seed")
        if payload.get("skip_guidelines"):
            command.append("--skip-guidelines")
        if config := payload.get("config"):
            command.extend(["--config", str(config)])
        return self._run_script(command, payload)

    # ------------------------------------------------------------------
    # Script helpers
    # ------------------------------------------------------------------
    def _run_script(self, command: list[str], payload: Dict[str, Any]) -> Dict[str, Any]:
        start = time.time()
        logger.info("Running script: %s", " ".join(map(str, command)))
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")

        try:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(self._scripts_dir),
                env=env,
            )
        except FileNotFoundError as exc:
            logger.error("Failed to start script %s: %s", command[0], exc)
            return {
                "exit_code": 127,
                "stdout": "",
                "stderr": str(exc),
                "duration": 0.0,
            }

        try:
            stdout, stderr = proc.communicate(timeout=self._timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            logger.warning(
                "Script %s timed out after %ss (pid=%s)",
                command[0],
                self._timeout_seconds,
                proc.pid,
            )
            return {
                "exit_code": -1,
                "stdout": stdout,
                "stderr": f"Script timed out after {self._timeout_seconds}s\n{stderr}",
                "duration": time.time() - start,
            }

        duration = time.time() - start
        logger.info("Script %s exited with code %s in %.2fs", command[0], proc.returncode, duration)

        # Best-effort structured extraction from stdout/stderr
        parsed_stdout = self._extract_json(stdout)
        parsed_stderr = self._extract_json(stderr)

        return {
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration": duration,
            "stdout_json": parsed_stdout,
            "stderr_json": parsed_stderr,
            "payload": payload,
        }

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        """Best-effort extraction of a JSON object from script output."""
        text = text.strip()
        if not text:
            return None
        # Look for a JSON object anywhere in the output (last one wins)
        candidates = re.findall(r"(\{.*\})", text, flags=re.DOTALL)
        for raw in reversed(candidates):
            try:
                obj = json.loads(raw)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
        return None

    def _load_timeout_config(self) -> int:
        """Load script timeout from environment with sane bounds."""
        default_timeout = 1800  # 30min
        min_timeout = 60
        raw = os.getenv("CHL_OPERATIONS_TIMEOUT_SEC")
        if not raw:
            return default_timeout
        try:
            configured = int(raw)
        except ValueError:
            logger.warning(
                "Invalid CHL_OPERATIONS_TIMEOUT_SEC=%r; using default %ds",
                raw,
                default_timeout,
            )
            return default_timeout
        if configured < min_timeout:
            logger.warning(
                "CHL_OPERATIONS_TIMEOUT_SEC=%d below minimum %ds, using minimum",
                configured,
                min_timeout,
            )
            return min_timeout
        return configured


__all__ = [
    "OperationsService",
    "OperationConflict",
    "JobNotFoundError",
    "OperationHandler",
]
