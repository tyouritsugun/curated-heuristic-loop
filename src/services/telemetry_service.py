"""Telemetry publisher for queue depth, worker health, and job progress."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from src.storage.schema import (
    TelemetrySample,
    WorkerMetric,
    JobHistory,
    utc_now,
)
from src.services.telemetry_names import QUEUE_DEPTH, WORKER_POOL

logger = logging.getLogger(__name__)


class TelemetryService:
    """Collects background samples and exposes snapshots to the API."""

    def __init__(
        self,
        session_factory,
        queue_probe: Callable[[], Optional[Dict[str, Any]]],
        worker_probe: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
        interval_seconds: int = 5,
        retention_per_metric: int = 288,
        meta_provider: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
    ):
        self._session_factory = session_factory
        self._queue_probe = queue_probe
        self._worker_probe = worker_probe or (lambda: None)
        self._interval = max(1, interval_seconds)
        self._retention = max(1, retention_per_metric)
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        # Optional provider for attaching metadata to snapshots (e.g., search_mode)
        self._meta_provider = meta_provider or (lambda: None)

    # ------------------------------------------------------------------
    # Lifecycle management
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if self._task:
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop())
        logger.info("TelemetryService loop started (interval=%ss)", self._interval)

    async def stop(self) -> None:
        if not self._task or not self._stop_event:
            return
        self._stop_event.set()
        await self._task
        self._task = None
        self._stop_event = None
        logger.info("TelemetryService loop stopped")

    async def _run_loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                self.collect_once()
            except Exception as exc:  # pragma: no cover - defensive guard
                logger.warning("Telemetry collection failed: %s", exc, exc_info=True)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                continue

    # ------------------------------------------------------------------
    # Sampling + snapshots
    # ------------------------------------------------------------------
    def collect_once(self) -> None:
        queue_sample = self._safe_call(self._queue_probe)
        worker_sample = self._safe_call(self._worker_probe)

        session = self._session_factory()
        try:
            if queue_sample is not None:
                self._insert_sample(session, QUEUE_DEPTH, queue_sample)
            if worker_sample is not None:
                self._insert_sample(session, WORKER_POOL, worker_sample)
                self._record_worker_metrics(session, worker_sample, queue_sample)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def snapshot(self, session: Session, jobs_limit: int = 5) -> Dict[str, Any]:
        queue = self._latest_sample(session, QUEUE_DEPTH)
        worker_pool = self._latest_sample(session, WORKER_POOL)
        workers = self._current_worker_metrics(session)
        jobs = self._recent_jobs(session, jobs_limit)

        # Attach optional metadata for dashboards (non-critical)
        meta: Optional[Dict[str, Any]] = None
        try:
            meta_candidate = self._meta_provider()
            if isinstance(meta_candidate, dict):
                meta = meta_candidate
        except Exception:
            # Never fail snapshots due to meta issues
            logger.debug("telemetry meta_provider failed", exc_info=True)

        return {
            "queue": queue,
            "worker_pool": worker_pool,
            "workers": workers,
            "jobs": jobs,
            "meta": meta,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _safe_call(self, fn: Callable[[], Optional[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
        try:
            return fn()
        except Exception as exc:
            logger.debug("Telemetry probe failed: %s", exc)
            return None

    def _insert_sample(self, session: Session, metric: str, value: Dict[str, Any]):
        """Insert telemetry sample with retry logic for database lock contention."""
        import time
        from sqlalchemy.exc import OperationalError

        max_retries = 6
        base_delay = 0.2  # 200ms, exponential backoff

        for attempt in range(max_retries):
            try:
                session.add(
                    TelemetrySample(
                        metric=metric,
                        value_json=json.dumps(value, ensure_ascii=False),
                        recorded_at=utc_now(),
                    )
                )
                session.flush()
                self._prune_samples(session, metric)
                return  # Success - exit retry loop

            except OperationalError as e:
                # Check if it's a database lock error
                if "database is locked" in str(e):
                    if attempt < max_retries - 1:
                        # Exponential backoff: 100ms, 200ms, 400ms
                        delay = base_delay * (2 ** attempt)
                        logger.debug(
                            f"Database locked when inserting telemetry sample, "
                            f"retrying in {delay:.3f}s (attempt {attempt + 1}/{max_retries})"
                        )
                        try:
                            session.rollback()
                        except Exception:
                            pass
                        time.sleep(delay)
                        continue
                    else:
                        # Final attempt failed - log but don't raise (telemetry is non-critical)
                        logger.warning(
                            f"Failed to insert telemetry sample after {max_retries} retries: {e}"
                        )
                        try:
                            session.rollback()
                        except Exception:
                            pass
                        return
                else:
                    # Different operational error - don't retry
                    raise

            except Exception as e:
                # Non-operational error - don't retry
                logger.warning(f"Failed to insert telemetry sample: {e}")
                try:
                    session.rollback()
                except Exception:
                    pass
                raise

    def _prune_samples(self, session: Session, metric: str):
        # Delete samples beyond retention window per metric
        ids = (
            session.query(TelemetrySample.id)
            .filter(TelemetrySample.metric == metric)
            .order_by(TelemetrySample.id.desc())
            .offset(self._retention)
            .all()
        )
        if ids:
            session.query(TelemetrySample).filter(TelemetrySample.id.in_([row[0] for row in ids])).delete(synchronize_session=False)

    def _record_worker_metrics(self, session: Session, pool_status: Dict[str, Any], queue_sample: Optional[Dict[str, Any]]):
        queue_total = 0
        if queue_sample:
            queue_total = queue_sample.get("pending", {}).get("total", 0)
        for worker in pool_status.get("workers", []):
            worker_id = str(worker.get("worker_id"))
            status = "paused" if worker.get("paused") else ("running" if worker.get("running") else "idle")
            heartbeat = utc_now()
            record = session.query(WorkerMetric).filter(WorkerMetric.worker_id == worker_id).one_or_none()
            payload = json.dumps(worker, ensure_ascii=False)
            if record is None:
                record = WorkerMetric(
                    worker_id=worker_id,
                    status=status,
                    heartbeat_at=heartbeat,
                    queue_depth=queue_total,
                    processed=int(worker.get("jobs_processed", 0)),
                    failed=int(worker.get("jobs_failed", 0)),
                    payload=payload,
                    created_at=heartbeat,
                )
                session.add(record)
            else:
                record.status = status
                record.heartbeat_at = heartbeat
                record.queue_depth = queue_total
                record.processed = int(worker.get("jobs_processed", 0))
                record.failed = int(worker.get("jobs_failed", 0))
                record.payload = payload
        # Clean up metrics for workers that disappeared
        workers = pool_status.get("workers") or []
        known_ids = {str(worker.get("worker_id")) for worker in workers}
        if not known_ids:
            session.query(WorkerMetric).delete(synchronize_session=False)
        else:
            session.query(WorkerMetric).filter(~WorkerMetric.worker_id.in_(known_ids)).delete(synchronize_session=False)

    def _latest_sample(self, session: Session, metric: str) -> Optional[Dict[str, Any]]:
        row = (
            session.query(TelemetrySample)
            .filter(TelemetrySample.metric == metric)
            .order_by(TelemetrySample.recorded_at.desc())
            .first()
        )
        if not row:
            return None
        return {
            "metric": metric,
            "recorded_at": row.recorded_at,
            "value": json.loads(row.value_json),
        }

    def _current_worker_metrics(self, session: Session):
        rows = session.query(WorkerMetric).order_by(WorkerMetric.worker_id.asc()).all()
        return [
            {
                "worker_id": row.worker_id,
                "status": row.status,
                "heartbeat_at": row.heartbeat_at,
                "queue_depth": row.queue_depth,
                "processed": row.processed,
                "failed": row.failed,
                "payload": json.loads(row.payload) if row.payload else None,
            }
            for row in rows
        ]

    def _recent_jobs(self, session: Session, limit: int):
        rows = (
            session.query(JobHistory)
            .order_by(JobHistory.created_at.desc())
            .limit(limit)
            .all()
        )
        results = []
        for row in rows:
            entry = {
                "job_id": row.job_id,
                "job_type": row.job_type,
                "status": row.status,
                "created_at": row.created_at,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "cancelled_at": row.cancelled_at,
            }
            if row.result:
                try:
                    entry["result"] = json.loads(row.result)
                except json.JSONDecodeError:
                    entry["result"] = row.result
            if row.error_detail:
                entry["error"] = row.error_detail
            results.append(entry)
        return results
