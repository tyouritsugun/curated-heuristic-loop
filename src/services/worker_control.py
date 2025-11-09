"""Worker control helpers exposed through the API."""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from src.storage.schema import AuditLog, CategoryManual, Experience, utc_now


class WorkerUnavailableError(RuntimeError):
    """Raised when worker pool interactions are requested but not initialized."""


class WorkerControlService:
    """Wraps worker pool operations with queue insights and audit logging."""

    def __init__(self, session_factory, pool_getter=None):
        self._session_factory = session_factory
        self._pool_getter = pool_getter or (lambda: None)

    def set_pool_getter(self, pool_getter):
        """Dynamically override the worker pool getter."""
        self._pool_getter = pool_getter or (lambda: None)

    def status(self, session: Session) -> Dict[str, Any]:
        """Return combined queue + worker status snapshot."""
        queue = self._queue_depth(session)
        pool_status = self._pool_status()
        return {"queue": queue, "workers": pool_status}

    def pause(self, session: Session, actor: Optional[str]) -> Dict[str, str]:
        pool = self._require_pool()
        pool.pause_all()
        self._log(session, "workers.pause", actor, {"reason": "api_request"})
        return {"status": "paused"}

    def resume(self, session: Session, actor: Optional[str]) -> Dict[str, str]:
        pool = self._require_pool()
        pool.resume_all()
        self._log(session, "workers.resume", actor, {"reason": "api_request"})
        return {"status": "resumed"}

    def drain(self, session: Session, timeout: int, actor: Optional[str]) -> Dict[str, Any]:
        pool = self._require_pool()
        start = time.time()
        while time.time() - start < timeout:
            pending = self._queue_depth(session)["pending"]["total"]
            if pending == 0:
                self._log(session, "workers.drain", actor, {"elapsed": time.time() - start})
                return {"status": "drained", "elapsed": time.time() - start}
            time.sleep(2)
        remaining = self._queue_depth(session)["pending"]["total"]
        return {"status": "timeout", "elapsed": timeout, "remaining": remaining}

    def queue_depth(self, session: Session) -> Dict[str, Any]:
        return self._queue_depth(session)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _queue_depth(self, session: Session) -> Dict[str, Any]:
        # Pending and processing are tracked separately for smoother UI feedback
        pending_exp = session.query(Experience).filter(Experience.embedding_status == "pending").count()
        pending_man = session.query(CategoryManual).filter(CategoryManual.embedding_status == "pending").count()
        processing_exp = session.query(Experience).filter(Experience.embedding_status == "processing").count()
        processing_man = session.query(CategoryManual).filter(CategoryManual.embedding_status == "processing").count()
        failed_exp = session.query(Experience).filter(Experience.embedding_status == "failed").count()
        failed_man = session.query(CategoryManual).filter(CategoryManual.embedding_status == "failed").count()
        return {
            "pending": {
                "experiences": pending_exp,
                "manuals": pending_man,
                "total": pending_exp + pending_man,
            },
            "processing": {
                "experiences": processing_exp,
                "manuals": processing_man,
                "total": processing_exp + processing_man,
            },
            "failed": {
                "experiences": failed_exp,
                "manuals": failed_man,
                "total": failed_exp + failed_man,
            },
        }

    def _pool_status(self) -> Optional[Dict[str, Any]]:
        pool = self._pool_getter()
        if not pool:
            return None
        return pool.get_status()

    def _require_pool(self):
        pool = self._pool_getter()
        if not pool:
            raise WorkerUnavailableError("Worker pool not initialized")
        return pool

    def _log(self, session: Session, event_type: str, actor: Optional[str], context: Dict[str, Any]):
        session.add(
            AuditLog(
                event_type=event_type,
                actor=actor,
                context=json.dumps(context, ensure_ascii=False),
                created_at=utc_now(),
            )
        )
